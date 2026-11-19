"""Parse and format Kubernetes resource quantities (cpu, memory)."""
import re

_MEM = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
        "k": 10**3, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "": 1}


def parse_mem(value):
    """'100Mi' -> bytes (int)."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", str(value).strip())
    if not m or m.group(2) not in _MEM:
        raise ValueError(f"Unrecognised memory quantity: {value!r}")
    return int(float(m.group(1)) * _MEM[m.group(2)])


def fmt_mem(nbytes):
    """bytes -> '200Mi' (rounded UP to a whole Mi so limits never shrink)."""
    mi = -(-int(nbytes) // 2**20)
    if mi % 1024 == 0:
        return f"{mi // 1024}Gi"
    return f"{mi}Mi"


def parse_cpu(value):
    """'250m' -> 250, '2' -> 2000, '0.5' -> 500 (millicores)."""
    s = str(value).strip()
    if s.endswith("m"):
        return int(float(s[:-1]))
    return int(round(float(s) * 1000))


def fmt_cpu(millicores):
    millicores = int(millicores)
    return str(millicores // 1000) if millicores % 1000 == 0 else f"{millicores}m"
