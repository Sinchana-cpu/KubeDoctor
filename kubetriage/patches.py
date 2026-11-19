"""Draft a strategic-merge patch for a Deployment from a runbook fix.

The agent never invents values it cannot know (for example the value of a
missing environment variable): those are reported as ``needs_input`` and must
be supplied by the human with --set.
"""
import re
from dataclasses import dataclass, field

from .units import fmt_cpu, fmt_mem, parse_cpu, parse_mem

IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(@sha256:[a-f0-9]{64})?$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class PatchPlan:
    patch: dict = None
    summary: str = ""
    changes: list = field(default_factory=list)      # (field path, before, after)
    needs_input: list = field(default_factory=list)  # keys the human must provide via --set
    notes: list = field(default_factory=list)
    reason_none: str = ""                             # why no patch could be drafted

    @property
    def ready(self):
        return self.patch is not None and not self.needs_input


def _container(dep, name):
    for c in dep["spec"]["template"]["spec"]["containers"]:
        if c["name"] == name:
            return c
    return None


def _wrap(container_name, body):
    return {"spec": {"template": {"spec": {"containers": [dict(name=container_name, **body)]}}}}


def _image_repo(image):
    image = image.split("@", 1)[0]
    last = image.rsplit("/", 1)[-1]
    return image.rsplit(":", 1)[0] if ":" in last else image


def build(rule, problem, dep, nodes, evidence, params):
    """Return a PatchPlan (possibly with reason_none / needs_input)."""
    fix = rule["fix"]
    kind = fix["kind"]
    if kind == "manual":
        return PatchPlan(reason_none="This failure needs a human decision; see the manual steps.")
    if dep is None:
        return PatchPlan(reason_none="The owning Deployment could not be read, so no patch was drafted.")
    c = _container(dep, problem.container)
    if c is None:
        return PatchPlan(reason_none=f"Container '{problem.container}' not found in the Deployment spec.")
    fn = {"set_image": _set_image, "set_env": _set_env, "raise_memory_limit": _raise_memory,
          "reduce_request": _reduce_request}.get(kind)
    if fn is None:
        return PatchPlan(reason_none=f"Runbook fix kind '{kind}' is not implemented.")
    plan = fn(fix, problem, c, nodes, evidence, params)
    plan.summary = plan.summary or fix["summary"]
    return plan


def _set_image(fix, problem, c, nodes, evidence, params):
    current = c.get("image", "")
    new = params.get("image") or fix.get("known_good", {}).get(_image_repo(current))
    if not new:
        return PatchPlan(needs_input=["image"], reason_none=f"No known-good tag for '{_image_repo(current)}'.",
                         notes=[f"Provide a valid image with --set image=<name:tag> (current: {current})."])
    if not IMAGE_RE.match(new):
        return PatchPlan(reason_none=f"'{new}' does not look like a valid image reference.")
    if new == current:
        return PatchPlan(reason_none="The suggested image equals the current one; nothing to change.")
    src = "supplied with --set" if params.get("image") else "known-good tag from the runbook"
    return PatchPlan(patch=_wrap(c["name"], {"image": new}),
                     changes=[(f"container '{c['name']}' image", current, new)],
                     notes=[f"New image comes from the {src}. Confirm it matches what your application needs."])


def _env_name(fix, evidence):
    text = evidence.get("logs", "")
    for pat in fix.get("env_name_patterns", []):
        m = re.search(pat, text, re.M)
        if m:
            return m.group(1)
    return None


def _set_env(fix, problem, c, nodes, evidence, params):
    name = _env_name(fix, evidence)
    if not name or not ENV_NAME_RE.match(name):
        return PatchPlan(reason_none="Could not work out which setting is missing from the log.")
    if any(e.get("name") == name for e in c.get("env", []) or []):
        return PatchPlan(reason_none=f"{name} is already defined on the container; the cause is probably its value.")
    value = params.get(f"env.{name}")
    if value is None:
        return PatchPlan(needs_input=[f"env.{name}"], reason_none=f"The value of {name} is unknown.",
                         notes=[f"The log says {name} is required. The agent will not guess its value: "
                                f"supply it with --set env.{name}=<value> (or answer the prompt)."])
    return PatchPlan(patch=_wrap(c["name"], {"env": [{"name": name, "value": value}]}),
                     changes=[(f"container '{c['name']}' env {name}", "(not set)", value)],
                     notes=["The value was supplied by you; the agent did not choose it."])


def _raise_memory(fix, problem, c, nodes, evidence, params):
    limit = (c.get("resources", {}) or {}).get("limits", {}).get("memory")
    if not limit:
        return PatchPlan(reason_none="The container has no memory limit, so the OOM kill came from the node itself; "
                                     "check node memory pressure instead.")
    cur = parse_mem(limit)
    new = int(cur * fix.get("factor", 2))
    cap = parse_mem(fix.get("max", "4Gi"))
    notes = []
    if params.get("memory"):
        new = parse_mem(params["memory"])
        notes.append("New limit supplied with --set memory=...")
    if new > cap:
        new = cap
        notes.append(f"Capped at {fix.get('max', '4Gi')} - a larger limit should be a deliberate decision.")
    if nodes:
        biggest = max(parse_mem(n["allocatable"]["memory"]) for n in nodes)
        if new > biggest:
            notes.append("The new limit exceeds the largest node's memory; the pod may not schedule.")
    if new <= cur:
        return PatchPlan(reason_none="The limit is already at or above the allowed maximum; investigate a memory leak.")
    new_s = fmt_mem(new)
    notes.append("A larger limit fixes 'too small' but only delays the kill if the app leaks memory. "
                 "Measure with 'kubectl top pod' when you can.")
    changes = [(f"container '{c['name']}' resources.limits.memory", limit, new_s)]
    body = {"resources": {"limits": {"memory": new_s}}}
    req = (c.get("resources", {}) or {}).get("requests", {}).get("memory")
    if req and parse_mem(req) > new:
        body["resources"]["requests"] = {"memory": new_s}
        changes.append((f"container '{c['name']}' resources.requests.memory", req, new_s))
    return PatchPlan(patch=_wrap(c["name"], body), changes=changes, notes=notes)


def _reduce_request(fix, problem, c, nodes, evidence, params):
    text = evidence.get("events", "") + "\n" + evidence.get("describe", "")
    wanted = [r for r in ("cpu", "memory") if re.search(rf"Insufficient {r}", text, re.I)]
    if not wanted:
        return PatchPlan(reason_none="The scheduler message does not name cpu or memory.")
    if not nodes:
        return PatchPlan(reason_none="Node capacity is unknown (could not read nodes), so a safe size cannot be computed.")
    requests = (c.get("resources", {}) or {}).get("requests", {})
    body, changes, notes = {}, [], []
    for res in wanted:
        cur = requests.get(res)
        if cur is None:
            continue
        if res == "cpu":
            biggest = max(parse_cpu(n["allocatable"]["cpu"]) for n in nodes)
            target = max(fix.get("cpu_floor_millicores", 50), int(biggest * fix.get("cpu_fraction_of_largest_node", 0.1)))
            target = (target // 50) * 50 or 50
            if target >= parse_cpu(cur):
                notes.append(f"The cpu request ({cur}) already fits a node; the cluster is probably full - add capacity.")
                continue
            new = fmt_cpu(target)
        else:
            biggest = max(parse_mem(n["allocatable"]["memory"]) for n in nodes)
            target = int(biggest * fix.get("memory_fraction_of_largest_node", 0.25))
            if target >= parse_mem(cur):
                notes.append(f"The memory request ({cur}) already fits a node; the cluster is probably full - add capacity.")
                continue
            new = fmt_mem(target)
        body.setdefault("resources", {}).setdefault("requests", {})[res] = new
        changes.append((f"container '{c['name']}' resources.requests.{res}", cur, new))
    if not body:
        return PatchPlan(reason_none="Lowering the request would not help.", notes=notes)
    notes.append("This size is a starting point derived from node capacity, not a measurement of what the app needs. "
                 "Check the units too: '500m' is half a core, '500' is five hundred cores.")
    return PatchPlan(patch=_wrap(c["name"], body), changes=changes, notes=notes)


# ---- minimal YAML emitter (no PyYAML dependency) ---------------------------------

def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    import json
    s = str(v)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./-]*", s) and s.lower() not in {"true", "false", "null", "yes", "no", "on", "off", "~"}:
        return s
    return json.dumps(s)


def to_yaml(obj, indent=0):
    pad = "  " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(to_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_scalar(v) if not isinstance(v, (dict, list)) else ('{}' if isinstance(v, dict) else '[]')}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                inner = to_yaml(item, indent + 1).split("\n")
                lines.append(f"{pad}- {inner[0].lstrip()}")
                lines.extend(inner[1:])
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return "\n".join(lines)


def render_patch_file(plan, header_comment):
    return "# " + header_comment.replace("\n", "\n# ") + "\n" + to_yaml(plan.patch) + "\n"
