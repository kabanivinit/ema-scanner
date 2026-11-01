from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def load(name):
    doc = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    doc["on"] = doc.get("on", doc.get(True))             # PyYAML reads the key `on` as True
    return doc


def test_three_workflows_exist_and_parse():
    assert sorted(p.name for p in WF.glob("*.yml")) == ["check-setup.yml", "daily-report.yml", "scan.yml"]
    for p in WF.glob("*.yml"):
        doc = load(p.name)
        assert "workflow_dispatch" in doc["on"] and doc["jobs"]


def test_scan_runs_every_quarter_hour_after_the_candle_closes():
    cron = load("scan.yml")["on"]["schedule"][0]["cron"].split()
    assert len(cron) == 5
    minutes = [int(m) for m in cron[0].split(",")]
    assert minutes == [3, 18, 33, 48]                    # 3 minutes after :00/:15/:30/:45 closes


def test_secrets_are_only_referenced_never_written_in_files():
    for p in WF.glob("*.yml"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if "TELEGRAM_" in line and ":" in line and "secrets." not in line and "env" not in line:
                raise AssertionError(f"{p.name}: {line}")
        assert "123456" not in p.read_text()


def test_state_cache_is_restored_and_always_saved_and_scan_is_serialised():
    for name in ("scan.yml", "daily-report.yml"):
        doc = load(name)
        steps = next(iter(doc["jobs"].values()))["steps"]
        uses = [s.get("uses", "") for s in steps]
        assert "actions/cache/restore@v4" in uses and "actions/cache/save@v4" in uses
        assert doc["concurrency"]["group"] == "ema-scanner" and doc["concurrency"]["cancel-in-progress"] is False
    assert load("daily-report.yml")["permissions"]["contents"] == "write"
    assert load("scan.yml")["permissions"]["contents"] == "read"
