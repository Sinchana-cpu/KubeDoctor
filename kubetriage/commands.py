"""Builders for every kubectl command the agent can issue (one place, so the
recorded-fixture generator and the agent can never drift apart)."""


def get_pods(namespace=None):
    return ["get", "pods", "-A", "-o", "json"] if not namespace else ["get", "pods", "-n", namespace, "-o", "json"]


def get_pods_selector(namespace, selector):
    return ["get", "pods", "-n", namespace, "-l", selector, "-o", "json"]


def describe_pod(namespace, pod):
    return ["describe", "pod", pod, "-n", namespace]


def pod_events(namespace, pod):
    return ["get", "events", "-n", namespace, "--field-selector", f"involvedObject.name={pod}",
            "--sort-by=.lastTimestamp"]


def pod_logs(namespace, pod, container, previous):
    args = ["logs", pod, "-n", namespace, "-c", container, "--tail=50"]
    if previous:
        args.append("--previous")
    return args


def get_deployment(namespace, name):
    return ["get", "deployment", name, "-n", namespace, "-o", "json"]


def get_nodes():
    return ["get", "nodes", "-o", "json"]


def rollout_status(namespace, name, timeout="120s"):
    return ["rollout", "status", f"deployment/{name}", "-n", namespace, f"--timeout={timeout}"]


def selector_string(match_labels):
    return ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))


def display(args):
    """Human-friendly 'kubectl ...' string."""
    return "kubectl " + " ".join(a if " " not in a else f'"{a}"' for a in args)
