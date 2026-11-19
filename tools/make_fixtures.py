#!/usr/bin/env python3
"""Regenerate fixtures/ (recorded clusters) and workloads/ (real manifests) from ONE definition.

Run:  python tools/make_fixtures.py
The same container specs feed both outputs, so the recorded demo and a real
kind/Minikube run describe the same broken Deployments.
"""
import copy
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kubetriage import commands as C            # noqa: E402
from kubetriage.patches import to_yaml          # noqa: E402

NS = "triage-lab"
NODE = "triage-lab-control-plane"
T0 = "2026-11-19T10:00:00Z"
CTX = "recorded-fixture (no live cluster)"

CRASH_SCRIPT = ('echo "crashy-api starting"; if [ -z "$APP_MODE" ]; then '
                'echo "FATAL: required environment variable APP_MODE is not set"; exit 1; fi; '
                'echo "running in $APP_MODE mode"; sleep 3600')


def W(dep, app, rs, pod, kind, container, after, describe, events, logs, requires, comment, pod_after, rs_after):
    return dict(dep=dep, app=app, rs=rs, pod=pod, kind=kind, container=container, after=after, describe=describe,
                events=events, logs=logs, requires=requires, comment=comment, pod_after=pod_after, rs_after=rs_after)


def container_spec(name, image, **kw):
    c = {"name": name, "image": image}
    c.update(kw)
    return c


# ------------------------------------------------------------------ descriptions
def describe_common(pod, dep, rs, status, container_block, conditions, events):
    return f"""Name:             {pod}
Namespace:        {NS}
Priority:         0
Node:             {NODE}/172.18.0.2
Start Time:       Thu, 19 Nov 2026 10:00:00 +0000
Labels:           app={dep}
                  pod-template-hash={rs.rsplit('-', 1)[1]}
Status:           {status}
IP:               10.244.0.{abs(hash(dep)) % 200 + 5}
Controlled By:    ReplicaSet/{rs}
Containers:
{container_block}
Conditions:
{conditions}
QoS Class:        Burstable
Node-Selectors:   <none>
Tolerations:      node.kubernetes.io/not-ready:NoExecute op=Exists for 300s
                  node.kubernetes.io/unreachable:NoExecute op=Exists for 300s
Events:
{events}
"""


def image_workload():
    img = "nginx:99.99-definitely-missing"
    dep, rs, pod = "broken-web", "broken-web-6b8b94c6f7", "broken-web-6b8b94c6f7-xk2qp"
    full = f"docker.io/library/{img}"
    block = f"""  web:
    Container ID:
    Image:          {img}
    Image ID:
    Port:           80/TCP
    Host Port:      0/TCP
    State:          Waiting
      Reason:       ImagePullBackOff
    Ready:          False
    Restart Count:  0
    Environment:    <none>"""
    cond = "  Type              Status\n  Initialized       True\n  Ready             False\n  ContainersReady   False\n  PodScheduled      True"
    err = (f'Failed to pull image "{img}": rpc error: code = NotFound desc = failed to pull and unpack image "{full}": '
           f'failed to resolve reference "{full}": {full}: not found')
    ev = f"""  Type     Reason     Age                From               Message
  ----     ------     ----               ----               -------
  Normal   Scheduled  3m                 default-scheduler  Successfully assigned {NS}/{pod} to {NODE}
  Normal   Pulling    78s (x4 over 3m)   kubelet            Pulling image "{img}"
  Warning  Failed     77s (x4 over 3m)   kubelet            {err}
  Warning  Failed     77s (x4 over 3m)   kubelet            Error: ErrImagePull
  Normal   BackOff    9s (x11 over 3m)   kubelet            Back-off pulling image "{img}"
  Warning  Failed     9s (x11 over 3m)   kubelet            Error: ImagePullBackOff"""
    events = f"""LAST SEEN   TYPE      REASON      OBJECT                                MESSAGE
3m          Normal    Scheduled   pod/{pod}   Successfully assigned {NS}/{pod} to {NODE}
77s         Warning   Failed      pod/{pod}   {err}
77s         Warning   Failed      pod/{pod}   Error: ErrImagePull
9s          Normal    BackOff     pod/{pod}   Back-off pulling image "{img}"
9s          Warning   Failed      pod/{pod}   Error: ImagePullBackOff"""
    c = container_spec("web", img, ports=[{"containerPort": 80}])
    after = copy.deepcopy(c)
    after["image"] = "nginx:1.27-alpine"
    return W(dep, dep, rs, pod, "imagepull", c, after, describe_common(pod, dep, rs, "Pending", block, cond, ev), events, None,
             ["nginx:1.27-alpine"], "BROKEN ON PURPOSE: this image tag does not exist (ImagePullBackOff).", "broken-web-5d7c9f6b84-m2lqz", "broken-web-5d7c9f6b84")


def crash_workload():
    dep, rs, pod = "crashy-api", "crashy-api-7f9d6c5b8d", "crashy-api-7f9d6c5b8d-q4w8z"
    c = container_spec("api", "busybox:1.36", command=["sh", "-c", CRASH_SCRIPT])
    after = copy.deepcopy(c)
    after["env"] = [{"name": "APP_MODE", "value": "staging"}]
    block = f"""  api:
    Container ID:  containerd://3f1c2a9b7e
    Image:         busybox:1.36
    Image ID:      docker.io/library/busybox@sha256:7b3ccabffc97de872a30dfd234fd972a66d247c8cfc69b0550f276481852627c
    Port:          <none>
    Host Port:     <none>
    Command:
      sh
      -c
      {CRASH_SCRIPT}
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
      Started:      Thu, 19 Nov 2026 10:04:12 +0000
      Finished:     Thu, 19 Nov 2026 10:04:12 +0000
    Ready:          False
    Restart Count:  5
    Environment:    <none>"""
    cond = "  Type              Status\n  Initialized       True\n  Ready             False\n  ContainersReady   False\n  PodScheduled      True"
    ev = f"""  Type     Reason     Age                  From               Message
  ----     ------     ----                 ----               -------
  Normal   Scheduled  5m                   default-scheduler  Successfully assigned {NS}/{pod} to {NODE}
  Normal   Pulled     2m (x5 over 5m)      kubelet            Container image "busybox:1.36" already present on machine
  Normal   Created    2m (x5 over 5m)      kubelet            Created container api
  Normal   Started    2m (x5 over 5m)      kubelet            Started container api
  Warning  BackOff    10s (x22 over 5m)    kubelet            Back-off restarting failed container api in pod {pod}_{NS}(7c1d)"""
    events = f"""LAST SEEN   TYPE      REASON      OBJECT                         MESSAGE
5m          Normal    Scheduled   pod/{pod}   Successfully assigned {NS}/{pod} to {NODE}
2m          Normal    Started     pod/{pod}   Started container api
10s         Warning   BackOff     pod/{pod}   Back-off restarting failed container api in pod {pod}_{NS}(7c1d)"""
    logs = 'crashy-api starting\nFATAL: required environment variable APP_MODE is not set'
    return W(dep, dep, rs, pod, "crash", c, after, describe_common(pod, dep, rs, "Running", block, cond, ev), events, logs,
             ["APP_MODE"], "BROKEN ON PURPOSE: the app exits because APP_MODE is not set (CrashLoopBackOff).",
             "crashy-api-6c8b7f5d9-h7k2p", "crashy-api-6c8b7f5d9")


def oom_workload():
    dep, rs, pod = "leaky-worker", "leaky-worker-5c7d9b6f44", "leaky-worker-5c7d9b6f44-n8x5t"
    res = {"requests": {"memory": "50Mi"}, "limits": {"memory": "100Mi"}}
    c = container_spec("worker", "polinux/stress", command=["stress"], args=["--vm", "1", "--vm-bytes", "150M", "--vm-hang", "1"], resources=res)
    after = copy.deepcopy(c)
    after["resources"]["limits"]["memory"] = "200Mi"
    block = """  worker:
    Container ID:  containerd://a91be44c20
    Image:         polinux/stress
    Image ID:      docker.io/polinux/stress@sha256:b6144f84f9c15dac80deb48d3a646b55c7043ab1d83ea0a697c09097aaad21aa
    Port:          <none>
    Host Port:     <none>
    Command:
      stress
    Args:
      --vm
      1
      --vm-bytes
      150M
      --vm-hang
      1
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      Thu, 19 Nov 2026 10:05:01 +0000
      Finished:     Thu, 19 Nov 2026 10:05:02 +0000
    Ready:          False
    Restart Count:  4
    Limits:
      memory:  100Mi
    Requests:
      memory:     50Mi
    Environment:  <none>"""
    cond = "  Type              Status\n  Initialized       True\n  Ready             False\n  ContainersReady   False\n  PodScheduled      True"
    ev = f"""  Type     Reason     Age                 From               Message
  ----     ------     ----                ----               -------
  Normal   Scheduled  4m                  default-scheduler  Successfully assigned {NS}/{pod} to {NODE}
  Normal   Pulled     90s (x5 over 4m)    kubelet            Container image "polinux/stress" already present on machine
  Normal   Created    90s (x5 over 4m)    kubelet            Created container worker
  Normal   Started    90s (x5 over 4m)    kubelet            Started container worker
  Warning  BackOff    8s (x18 over 4m)    kubelet            Back-off restarting failed container worker in pod {pod}_{NS}(2b9e)"""
    events = f"""LAST SEEN   TYPE      REASON      OBJECT                            MESSAGE
4m          Normal    Scheduled   pod/{pod}   Successfully assigned {NS}/{pod} to {NODE}
90s         Normal    Started     pod/{pod}   Started container worker
8s          Warning   BackOff     pod/{pod}   Back-off restarting failed container worker in pod {pod}_{NS}(2b9e)"""
    logs = "stress: info: [1] dispatching hogs: 0 cpu, 0 io, 1 vm, 0 hdd"
    return W(dep, dep, rs, pod, "oom", c, after, describe_common(pod, dep, rs, "Running", block, cond, ev), events, logs,
             ["200Mi"], "BROKEN ON PURPOSE: the app allocates ~150MB but the limit is 100Mi (OOMKilled).",
             "leaky-worker-7b5f8d6c59-r2j6v", "leaky-worker-7b5f8d6c59")


def pending_workload():
    dep, rs, pod = "hungry-batch", "hungry-batch-84c6f5d7b9", "hungry-batch-84c6f5d7b9-v6m2c"
    res = {"requests": {"cpu": "64"}}
    c = container_spec("batch", "nginx:1.27-alpine", resources=res)
    after = copy.deepcopy(c)
    after["resources"]["requests"]["cpu"] = "800m"
    block = """  batch:
    Image:      nginx:1.27-alpine
    Port:       <none>
    Host Port:  <none>
    Requests:
      cpu:        64
    Environment:  <none>"""
    cond = "  Type           Status\n  PodScheduled   False"
    msg = "0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod."
    ev = f"""  Type     Reason            Age   From               Message
  ----     ------            ----  ----               -------
  Warning  FailedScheduling  2m    default-scheduler  {msg}"""
    desc = f"""Name:             {pod}
Namespace:        {NS}
Priority:         0
Service Account:  default
Node:             <none>
Labels:           app={dep}
                  pod-template-hash=84c6f5d7b9
Status:           Pending
IP:
Controlled By:    ReplicaSet/{rs}
Containers:
{block}
Conditions:
{cond}
QoS Class:        Burstable
Node-Selectors:   <none>
Events:
{ev}
"""
    events = f"""LAST SEEN   TYPE      REASON             OBJECT                         MESSAGE
2m          Warning   FailedScheduling   pod/{pod}   {msg}"""
    return W(dep, dep, rs, pod, "pending", c, after, desc, events, None, ["800m"],
             "BROKEN ON PURPOSE: asks for 64 CPU cores, more than any node has (Pending).",
             "hungry-batch-6d9f7c8b5-t9p4d", "hungry-batch-6d9f7c8b5")


def healthy_workload():
    dep, rs, pod = "web-ok", "web-ok-69c7d8f5b6", "web-ok-69c7d8f5b6-8gk2s"
    c = container_spec("web", "nginx:1.27-alpine", ports=[{"containerPort": 80}])
    return W(dep, dep, rs, pod, "healthy", c, None, "", "", None, [], "Healthy control workload.", None, None)


# ------------------------------------------------------------------ JSON builders
def pod_status(w, after=False):
    c = w["container"]["name"]
    img = (w["after"] if after else w["container"])["image"]
    k = "running" if after else w["kind"]
    ready_conds = [{"type": t, "status": "True"} for t in ("Initialized", "Ready", "ContainersReady", "PodScheduled")]
    if k in ("running", "healthy"):
        return {"phase": "Running", "conditions": ready_conds,
                "containerStatuses": [{"name": c, "ready": True, "restartCount": 0, "image": img,
                                       "state": {"running": {"startedAt": T0}}}]}
    if k == "imagepull":
        return {"phase": "Pending",
                "conditions": [{"type": "PodScheduled", "status": "True"}, {"type": "Ready", "status": "False"}],
                "containerStatuses": [{"name": c, "ready": False, "restartCount": 0, "image": img, "imageID": "",
                                       "state": {"waiting": {"reason": "ImagePullBackOff", "message": f'Back-off pulling image "{img}"'}}}]}
    if k in ("crash", "oom"):
        term = {"exitCode": 1, "reason": "Error"} if k == "crash" else {"exitCode": 137, "reason": "OOMKilled"}
        term.update({"startedAt": T0, "finishedAt": T0})
        return {"phase": "Running", "conditions": [{"type": "Ready", "status": "False"}],
                "containerStatuses": [{"name": c, "ready": False, "restartCount": 5 if k == "crash" else 4, "image": img,
                                       "state": {"waiting": {"reason": "CrashLoopBackOff", "message": f"back-off 2m40s restarting failed container={c}"}},
                                       "lastState": {"terminated": term}}]}
    if k == "pending":
        return {"phase": "Pending", "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                "message": "0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod."}]}
    raise ValueError(k)


def pod_obj(w, after=False):
    rs = w["rs_after"] if after and w["rs_after"] else w["rs"]
    name = w["pod_after"] if after and w["pod_after"] else w["pod"]
    c = w["after"] if after and w["after"] else w["container"]
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": NS, "creationTimestamp": T0,
                         "labels": {"app": w["app"], "pod-template-hash": rs.rsplit("-", 1)[1]},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": rs, "controller": True}]},
            "spec": {"containers": [c], "nodeName": NODE if w["kind"] != "pending" or after else ""},
            "status": pod_status(w, after)}


def deployment_obj(w, after=False):
    c = w["after"] if after and w["after"] else w["container"]
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": w["dep"], "namespace": NS},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": w["app"]}},
                     "template": {"metadata": {"labels": {"app": w["app"]}}, "spec": {"containers": [c]}}}}


def nodes_obj():
    return {"apiVersion": "v1", "kind": "List", "items": [{
        "metadata": {"name": NODE},
        "status": {"allocatable": {"cpu": "8", "memory": "16254636Ki", "pods": "110"},
                   "conditions": [{"type": "Ready", "status": "True"}]}}]}


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2) + "\n", encoding="utf-8")


def render(name, description, workloads):
    base = ROOT / "fixtures" / name
    if base.exists():
        shutil.rmtree(base)
    cmds, after = {}, {}
    pods = {"apiVersion": "v1", "kind": "List", "items": [pod_obj(w) for w in workloads]}
    put(base / "before/pods.json", pods)
    cmds[" ".join(C.get_pods(NS))] = "before/pods.json"
    cmds[" ".join(C.get_pods(None))] = "before/pods.json"
    put(base / "before/nodes.json", nodes_obj())
    cmds[" ".join(C.get_nodes())] = "before/nodes.json"
    for w in workloads:
        if w["kind"] == "healthy":
            continue
        d = w["dep"]
        put(base / f"before/describe-{d}.txt", w["describe"])
        cmds[" ".join(C.describe_pod(NS, w["pod"]))] = f"before/describe-{d}.txt"
        put(base / f"before/events-{d}.txt", w["events"] + "\n")
        cmds[" ".join(C.pod_events(NS, w["pod"]))] = f"before/events-{d}.txt"
        if w["logs"]:
            put(base / f"before/logs-{d}.txt", w["logs"] + "\n")
            cmds[" ".join(C.pod_logs(NS, w["pod"], w["container"]["name"], previous=True))] = f"before/logs-{d}.txt"
        put(base / f"before/deployment-{d}.json", deployment_obj(w))
        cmds[" ".join(C.get_deployment(NS, d))] = f"before/deployment-{d}.json"
        sel = C.selector_string({"app": w["app"]})
        put(base / f"before/selector-{d}.json", {"apiVersion": "v1", "kind": "List", "items": [pod_obj(w)]})
        cmds[" ".join(C.get_pods_selector(NS, sel))] = f"before/selector-{d}.json"
    put(base / "manifest.json", {"scenario": name, "description": description, "context": CTX, "namespace": NS,
                                 "commands": cmds})


def write_workloads(workloads):
    wd = ROOT / "workloads"
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir()
    put(wd / "00-namespace.yaml", "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: triage-lab\n")
    for w in workloads:
        if w["kind"] == "healthy":
            continue
        obj = deployment_obj(w)
        obj["spec"]["template"]["metadata"]["labels"] = {"app": w["app"]}
        put(wd / ("broken-image.yaml" if w["dep"] == "broken-web" else f"{w['dep']}.yaml"), f"# {w['comment']}\n" + to_yaml(obj) + "\n")
    ok = healthy_workload()
    put(wd / "web-ok.yaml", "# Healthy control workload (should NOT be flagged).\n" + to_yaml(deployment_obj(ok)) + "\n")


def main():
    img, crash, oom, pend, ok = image_workload(), crash_workload(), oom_workload(), pending_workload(), healthy_workload()
    render("image-pull-backoff", "One Deployment asks for an nginx tag that does not exist (ImagePullBackOff).", [img])
    render("crashloop-missing-env", "An app exits at startup because APP_MODE is not set (CrashLoopBackOff).", [crash])
    render("oomkilled", "A worker uses more memory than its 100Mi limit (OOMKilled).", [oom])
    render("pending-insufficient-cpu", "A pod requests 64 CPU cores; no node can fit it (Pending).", [pend])
    render("all-four", "Four broken Deployments plus one healthy one in a single namespace.", [img, crash, oom, pend, ok])
    write_workloads([img, crash, oom, pend, ok])
    print("fixtures and workloads regenerated")


if __name__ == "__main__":
    main()
