"""Find unhealthy pods in `kubectl get pods -o json` output."""
from dataclasses import dataclass, field

WAITING_BAD = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
               "CreateContainerError", "InvalidImageName", "RunContainerError", "ContainerCannotRun"}


@dataclass
class Problem:
    namespace: str
    pod: str
    container: str
    symptom: str            # e.g. CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending
    detail: str = ""        # human-readable one-liner
    exit_code: int = None
    restarts: int = 0
    image: str = ""
    deployment: str = None
    ever_started: bool = False
    pods_affected: int = 1
    extra_pods: list = field(default_factory=list)

    @property
    def workload(self):
        return f"{self.namespace}/{self.deployment or self.pod}"

    @property
    def key(self):
        return (self.namespace, self.deployment or self.pod)


def _owner_deployment(meta):
    for o in meta.get("ownerReferences", []) or []:
        if o.get("kind") == "ReplicaSet" and "-" in o.get("name", ""):
            return o["name"].rsplit("-", 1)[0]
    return None


def classify(pod):
    """Return a Problem for an unhealthy pod, or None if it looks fine/finished."""
    meta, spec, st = pod.get("metadata", {}), pod.get("spec", {}), pod.get("status", {})
    if meta.get("deletionTimestamp"):
        return None
    phase = st.get("phase", "")
    if phase == "Succeeded":
        return None
    images = {c["name"]: c.get("image", "") for c in spec.get("containers", [])}
    base = dict(namespace=meta.get("namespace", ""), pod=meta.get("name", ""), deployment=_owner_deployment(meta))

    statuses = (st.get("initContainerStatuses") or []) + (st.get("containerStatuses") or [])
    for cs in statuses:
        state, last = cs.get("state", {}), cs.get("lastState", {})
        waiting, term, lterm = state.get("waiting"), state.get("terminated"), last.get("terminated")
        restarts = cs.get("restartCount", 0)
        started = bool(lterm or term or state.get("running") or restarts)
        common = dict(base, container=cs.get("name", ""), restarts=restarts, ever_started=started,
                      image=images.get(cs.get("name", ""), cs.get("image", "")))
        for t in (lterm, term):
            if t and t.get("reason") == "OOMKilled":
                return Problem(symptom="OOMKilled", exit_code=t.get("exitCode"),
                               detail=f"container '{cs['name']}' was killed for exceeding its memory limit "
                                      f"(exit {t.get('exitCode')}, {restarts} restarts)", **common)
        if waiting and waiting.get("reason") in WAITING_BAD:
            code = lterm.get("exitCode") if lterm else None
            extra = f", last exit code {code}" if code is not None else ""
            return Problem(symptom=waiting["reason"], exit_code=code,
                           detail=f"container '{cs['name']}' is waiting: {waiting['reason']} ({restarts} restarts{extra})",
                           **common)
        if term and term.get("exitCode", 0) != 0 and phase != "Running":
            return Problem(symptom="Error", exit_code=term.get("exitCode"),
                           detail=f"container '{cs['name']}' exited with code {term.get('exitCode')}", **common)

    for cond in st.get("conditions", []) or []:
        if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
            first = (spec.get("containers") or [{}])[0]
            return Problem(symptom="Pending", container=first.get("name", ""), image=first.get("image", ""),
                           detail=f"pod cannot be scheduled: {cond.get('message', cond.get('reason', ''))}", **base)
    if phase == "Failed":
        first = (spec.get("containers") or [{}])[0]
        return Problem(symptom="Failed", container=first.get("name", ""), image=first.get("image", ""),
                       detail=f"pod failed: {st.get('reason', '')} {st.get('message', '')}".strip(), **base)
    if phase == "Running" and statuses and not all(cs.get("ready") for cs in statuses):
        cs = next(c for c in statuses if not c.get("ready"))
        return Problem(symptom="NotReady", container=cs.get("name", ""), restarts=cs.get("restartCount", 0),
                       image=images.get(cs.get("name", ""), ""), ever_started=True,
                       detail=f"container '{cs.get('name')}' is running but not ready", **base)
    return None


def find_problems(pod_list_json):
    """Group unhealthy pods by owning deployment (one investigation per workload)."""
    groups = {}
    for pod in pod_list_json.get("items", []):
        p = classify(pod)
        if not p:
            continue
        if p.key in groups:
            groups[p.key].pods_affected += 1
            groups[p.key].extra_pods.append(p.pod)
        else:
            groups[p.key] = p
    return list(groups.values())


def pod_is_healthy(pod):
    st = pod.get("status", {})
    if pod.get("metadata", {}).get("deletionTimestamp") or st.get("phase") != "Running":
        return False
    statuses = st.get("containerStatuses") or []
    return bool(statuses) and all(c.get("ready") and "running" in c.get("state", {}) for c in statuses)
