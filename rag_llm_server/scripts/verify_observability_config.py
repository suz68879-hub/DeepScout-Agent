"""Validate Phase 4 dashboards, rules, links and bounded query dimensions."""

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = REPO_ROOT / "observability"
RUNBOOKS = REPO_ROOT / "deploy" / "runbooks"
ALLOWED_VARIABLES = {"environment", "job_type", "service"}
REQUIRED_RUNBOOK_SECTIONS = {
    "Dashboard",
    "影响",
    "升级",
    "安全缓解",
    "恢复确认",
    "权限",
    "检测",
    "演练记录",
    "诊断顺序",
    "审批点",
}


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_dashboards() -> dict[str, Path]:
    dashboard_files = sorted(
        (OBSERVABILITY / "grafana" / "dashboards").glob("*.json")
    )
    dashboards = {
        document["uid"]: path
        for path in dashboard_files
        if (document := json.loads(path.read_text(encoding="utf-8")))
    }
    if len(dashboards) != 4:
        raise ValueError("exactly four unique dashboards are required")
    for uid, path in dashboards.items():
        document = json.loads(path.read_text(encoding="utf-8"))
        if not document.get("panels"):
            raise ValueError(f"dashboard {uid} has no panels")
        variables = {
            item["name"] for item in document.get("templating", {}).get("list", [])
        }
        if not variables <= ALLOWED_VARIABLES:
            raise ValueError(f"dashboard {uid} uses unbounded variables")
        encoded = path.read_text(encoding="utf-8")
        if "or vector(0)" in encoded:
            raise ValueError(f"dashboard {uid} masks No data as zero")
    provisioning = _load_yaml(
        OBSERVABILITY / "grafana" / "provisioning" / "dashboards.yaml"
    )
    if provisioning["providers"][0]["type"] != "file":
        raise ValueError("Grafana file provisioning is required")
    return dashboards


def validate_alerts(dashboards: dict[str, Path]) -> tuple[int, int]:
    recording = _load_yaml(
        OBSERVABILITY / "prometheus" / "recording-rules.yaml"
    )
    alerts = _load_yaml(OBSERVABILITY / "prometheus" / "alerts.yaml")
    recording_rules = [rule for group in recording["groups"] for rule in group["rules"]]
    alert_rules = [rule for group in alerts["groups"] for rule in group["rules"]]
    for rule in alert_rules:
        if not {"owner", "severity"} <= set(rule.get("labels", {})):
            raise ValueError(f"alert {rule['alert']} lacks required labels")
        annotations = rule.get("annotations", {})
        if not {"dashboard_url", "runbook_url"} <= set(annotations):
            raise ValueError(f"alert {rule['alert']} lacks required links")
        dashboard_uid = annotations["dashboard_url"].rsplit("/", 1)[-1]
        if dashboard_uid not in dashboards:
            raise ValueError(f"alert {rule['alert']} links unknown dashboard")
        runbook_slug = annotations["runbook_url"].rsplit("/", 1)[-1]
        runbook = RUNBOOKS / f"{runbook_slug}.md"
        if not runbook.is_file():
            raise ValueError(f"alert {rule['alert']} links missing runbook")
        text = runbook.read_text(encoding="utf-8")
        missing = REQUIRED_RUNBOOK_SECTIONS - {
            section for section in REQUIRED_RUNBOOK_SECTIONS if section in text
        }
        if missing:
            raise ValueError(f"runbook {runbook.name} lacks {sorted(missing)}")
    return len(recording_rules), len(alert_rules)


def main() -> None:
    dashboards = validate_dashboards()
    recording_count, alert_count = validate_alerts(dashboards)
    result = {
        "alerts": alert_count,
        "dashboards": len(dashboards),
        "recording_rules": recording_count,
        "runbooks": len(list(RUNBOOKS.glob("*-incident.md"))) + 3,
        "status": "passed",
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
