from __future__ import annotations

import json
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pandas as pd

from voxmaps_sim.desktop_api import create_app


VALID_FLIGHT = b"""time(millisecond),datetime(utc),latitude,longitude,height_above_takeoff(feet),wind_speed(mph),wind_direction(degrees),flycState,flycStateRaw,max_altitude,rc_throttle
0,2026-04-14T09:28:00Z,28.60000,77.20000,10,5,270,Flying,7,10,0
1000,2026-04-14T09:28:01Z,28.60001,77.20001,12,6,275,Flying,7,12,0
2000,2026-04-14T09:28:02Z,28.60002,77.20002,14,7,280,Landing,8,14,0
"""


@dataclass
class FakeResult:
    simulation_id: str = "sim-test"
    mass_balance: dict[str, float] | None = None
    warnings: list[str] | None = None
    frame_count: int = 3

    def __post_init__(self) -> None:
        self.mass_balance = self.mass_balance or {"emitted_g": 1.0, "accounted_g": 1.0}
        self.warnings = self.warnings or []


def successful_runner(
    _flight_path: Path,
    _config: Any,
    output_dir: Path,
    *,
    scenario_name: str,
    progress_callback,
    cancel_check,
) -> FakeResult:
    progress_callback(0.1, "validating_csv", "Validated immutable upload")
    assert not cancel_check()
    progress_callback(0.75, "generating_playback", "Built playback frames")
    (output_dir / f"{scenario_name}_simulation_trace.h5").write_bytes(b"test-hdf5")
    (output_dir / f"{scenario_name}_simulated_drone_sensor_log.csv").write_text(
        "simulation_id,sample_id,pm25_true_ug_m3,pm10_true_ug_m3\nsim-test,0,1,2\n",
        encoding="utf-8",
    )
    return FakeResult()


def cancelling_runner(
    _flight_path: Path,
    _config: Any,
    output_dir: Path,
    *,
    scenario_name: str,
    progress_callback,
    cancel_check,
) -> FakeResult:
    class SimulationCancelled(RuntimeError):
        pass

    for index in range(200):
        if cancel_check():
            raise SimulationCancelled("cancelled")
        progress_callback(index / 200, "transporting_parcels", f"step {index}")
        time.sleep(0.005)
    (output_dir / f"{scenario_name}_simulation_trace.h5").write_bytes(b"unexpected")
    (output_dir / f"{scenario_name}_simulated_drone_sensor_log.csv").write_text(
        "unexpected", encoding="utf-8"
    )
    return FakeResult()


def client_for(tmp_path: Path, runner=successful_runner) -> TestClient:
    return TestClient(
        create_app(
            workspace_dir=tmp_path / "workspace",
            trace_runner=runner,
            frontend_dir=tmp_path / "missing-frontend",
        )
    )


def test_bundled_sih_demo_flight_and_trace(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        flight = client.post("/api/demo/load-flight")
        assert flight.status_code == 200, flight.text
        payload = flight.json()
        assert payload["provenance"] == "bundled_simulated_five_minute_input"
        assert payload["inspection"]["row_count"] == 3000
        assert Path(payload["output_folder"]).is_dir()
        assert client.app.state.uploads.get(payload["upload_id"]).path.is_file()

        trace = client.post("/api/demo/load-trace")
        assert trace.status_code == 200, trace.text
        assert trace.json()["playback_id"]
        assert trace.json()["metadata"]["frame_count"] == 60


def upload(
    client: TestClient,
    content: bytes = VALID_FLIGHT,
    filename: str = "flight.csv",
) -> dict[str, Any]:
    response = client.post(
        "/api/uploads",
        files={"file": (filename, content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def sfd_bytes() -> bytes:
    buffer = io.BytesIO()
    frame = pd.DataFrame(
        {
            "elapsed_time_ms": [1100, 1200, 1300],
            "utc_datetime": [
                "2026-04-14T03:58:29Z",
                "2026-04-14T03:58:29.100Z",
                "2026-04-14T03:58:29.200Z",
            ],
            "latitude": [28.5685, 28.5686, 28.5687],
            "longitude": [77.2773, 77.2774, 77.2775],
            "altitude(feet)": [750.0, 751.0, 752.0],
            "wind_speed_original(mph)": [None, 5.0, None],
            "wind_direction_original_from(degrees)": [None, 270.0, None],
            "wind_speed_complete(mph)": [4.0, 5.0, 6.0],
            "wind_direction_complete_from(degrees)": [260.0, 270.0, 280.0],
            "wind_data_source": [
                "simulated_leading_gap",
                "original_airdata_estimate",
                "interpolated_short_gap",
            ],
            "wind_confidence": [0.25, 1.0, 0.8],
            "wind_gap_id": ["G001", "", "G002"],
            "calm_flag": [False, False, False],
        }
    )
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Complete Wind Data", index=False)
    return buffer.getvalue()


def wait_for_terminal(client: TestClient, job_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["state"] in {"completed", "cancelled", "failed"}:
            return status
        time.sleep(0.01)
    raise AssertionError("job did not reach a terminal state")


def test_defaults_health_and_two_channel_aliases(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        config = client.get("/api/config/defaults").json()["config"]
        assert config["wind_policy"]["mode"] == "measured"
        assert config["emissions"]["pm25_emission_g_s"] == 0.2
        assert config["emissions"]["coarse_pm_emission_g_s"] == 0.3
        assert config["background"]["pm10_ug_m3"] == (
            config["background"]["pm25_ug_m3"]
            + config["engine"]["background"]["coarse_pm_ug_m3"]
        )


def test_upload_inspection_and_column_dispositions(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        payload = upload(client)
        inspection = payload["inspection"]
        assert inspection["row_count"] == 3
        assert inspection["duration_s"] == 2.0
        assert inspection["valid_paired_wind_coverage"] == 1.0
        assert inspection["requires_mapping"] is False
        retained = {item["column"]: item for item in inspection["retained_columns"]}
        excluded = {item["column"]: item for item in inspection["excluded_columns"]}
        assert retained["flycState"]["category"] == "flight_context"
        assert excluded["flycStateRaw"]["category"] == "status"
        assert excluded["max_altitude"]["category"] == "summary"
        assert excluded["rc_throttle"]["category"] == "control_input"
        assert len(inspection["preview_points"]) == 3


def test_sfd_xlsx_upload_is_inspected_as_complete_wind(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/uploads",
            files={
                "file": (
                    "flight-sfd.xlsx",
                    sfd_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert response.status_code == 200, response.text
        inspection = response.json()["inspection"]
        assert inspection["input_format"] == "sfd_xlsx"
        assert inspection["worksheet"] == "Complete Wind Data"
        assert inspection["row_count"] == 3
        assert inspection["valid_paired_wind_coverage"] == 1.0
        assert inspection["sfd_original_wind_rows"] == 1
        assert inspection["sfd_generated_wind_rows"] == 2
        assert inspection["requires_mapping"] is False


def test_mapping_fallback_can_be_resolved(tmp_path: Path) -> None:
    ambiguous = b"clock,when,northing,easting,up,ws,wd\n0,2026-01-01T00:00:00Z,28,77,10,2,90\n1,2026-01-01T00:00:01Z,28.1,77.1,11,2,90\n"
    with client_for(tmp_path) as client:
        payload = upload(client, ambiguous)
        assert payload["inspection"]["requires_mapping"] is True
        response = client.put(
            f"/api/uploads/{payload['upload_id']}/mapping",
            json={
                "mapping": {
                    "elapsed_time": "clock",
                    "timestamp_utc": "when",
                    "latitude": "northing",
                    "longitude": "easting",
                    "altitude": "up",
                    "wind_speed": "ws",
                    "wind_direction": "wd",
                },
                "min_wind_coverage": 0.8,
            },
        )
        assert response.status_code == 200
        assert response.json()["inspection"]["requires_mapping"] is False


def test_sparse_logged_wind_is_allowed_while_strict_mode_rejects(tmp_path: Path) -> None:
    sparse = VALID_FLIGHT.replace(b"6,275", b"N/A,N/A")
    with client_for(tmp_path) as client:
        payload = upload(client, sparse)
        config = client.get("/api/config/defaults").json()["config"]
        config["wind_policy"]["mode"] = "measured"
        logged = client.post(
            "/api/config/validate",
            json={"upload_id": payload["upload_id"], "config": config},
        ).json()
        assert logged["valid"] is True
        assert any("Logged measured wind" in item for item in logged["warnings"])

        config["wind_policy"]["mode"] = "strict_measured"
        strict = client.post(
            "/api/config/validate",
            json={"upload_id": payload["upload_id"], "config": config},
        ).json()
        assert strict["valid"] is False
        assert any("Measured wind cannot be used" in item for item in strict["errors"])
        config["wind_policy"]["mode"] = "synthetic_fallback"
        synthetic = client.post(
            "/api/config/validate",
            json={"upload_id": payload["upload_id"], "config": config},
        ).json()
        assert synthetic["valid"] is True
        assert any("Synthetic fallback" in item for item in synthetic["warnings"])
        assert synthetic["estimate"]["sensor_samples"] == 3


def test_background_pm10_identity_is_validated(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        config = client.get("/api/config/defaults").json()["config"]
        config["background"]["pm25_ug_m3"] = 20.0
        config["background"]["pm10_ug_m3"] = 10.0
        response = client.post(
            "/api/config/validate", json={"upload_id": None, "config": config}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert any("PM10" in item for item in response.json()["errors"])


def test_background_job_publishes_exactly_two_files(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        uploaded = upload(client, filename="AirData-original-name.csv")
        config = client.get("/api/config/defaults").json()["config"]
        config["wind_policy"]["mode"] = "measured"
        output_root = tmp_path / "outputs"
        response = client.post(
            "/api/jobs",
            json={
                "upload_id": uploaded["upload_id"],
                "scenario_name": "api-smoke",
                "output_folder": str(output_root),
                "config": config,
            },
        )
        assert response.status_code == 200, response.text
        status = wait_for_terminal(client, response.json()["job_id"])
        assert status["state"] == "completed", json.dumps(status, indent=2)
        scenario_dir = output_root / "api-smoke"
        assert sorted(path.name for path in scenario_dir.iterdir()) == [
            "api-smoke_simulated_drone_sensor_log.csv",
            "api-smoke_simulation_trace.h5",
        ]
        result = client.get(f"/api/jobs/{status['job_id']}/result").json()
        assert len(result["output_files"]) == 2
        assert result["mass_balance"]["emitted_g"] == 1.0


def test_cancellation_removes_incomplete_output(tmp_path: Path) -> None:
    with client_for(tmp_path, cancelling_runner) as client:
        uploaded = upload(client)
        config = client.get("/api/config/defaults").json()["config"]
        output_root = tmp_path / "cancel-output"
        created = client.post(
            "/api/jobs",
            json={
                "upload_id": uploaded["upload_id"],
                "scenario_name": "cancelled-run",
                "output_folder": str(output_root),
                "config": config,
            },
        )
        assert created.status_code == 200
        job_id = created.json()["job_id"]
        client.delete(f"/api/jobs/{job_id}")
        status = wait_for_terminal(client, job_id)
        assert status["state"] == "cancelled"
        assert not (output_root / "cancelled-run").exists()
        assert not list(output_root.glob("*.partial"))
        assert not list(output_root.glob(".*.partial"))


def test_configuration_save_load_and_noninteractive_folder_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    with client_for(tmp_path) as client:
        config = client.get("/api/config/defaults").json()["config"]
        target = tmp_path / "scenario.json"
        saved = client.post(
            "/api/config/save", json={"path": str(target), "config": config}
        )
        assert saved.status_code == 200
        assert json.loads(target.read_text(encoding="utf-8"))["wind_policy"]["mode"] == "measured"
        loaded = client.post("/api/config/load", json={"path": str(target)})
        assert loaded.status_code == 200
        assert loaded.json()["config"]["sensor_region"]["size_x_m"] == 20.0
        folder = client.post(
            "/api/folders/select",
            json={
                "use_native_dialog": False,
                "suggested_path": str(tmp_path / "chosen"),
            },
        ).json()
        assert folder["source"] == "fallback_default"
        assert Path(folder["path"]).is_dir()

        automated_output = tmp_path / "automated-browser-output"
        monkeypatch.setenv("VOXMAPS_DISABLE_NATIVE_DIALOG", "1")
        monkeypatch.setenv("VOXMAPS_DEFAULT_OUTPUT_DIR", str(automated_output))
        automated = client.post(
            "/api/folders/select", json={"use_native_dialog": True}
        ).json()
        assert automated == {
            "path": str(automated_output.resolve()),
            "cancelled": False,
            "source": "fallback_default",
        }
        assert automated_output.is_dir()


def test_scenario_name_and_existing_output_are_rejected(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        uploaded = upload(client)
        config = client.get("/api/config/defaults").json()["config"]
        invalid = client.post(
            "/api/jobs",
            json={
                "upload_id": uploaded["upload_id"],
                "scenario_name": "../escape",
                "output_folder": str(tmp_path),
                "config": config,
            },
        )
        assert invalid.status_code == 422
        (tmp_path / "existing").mkdir()
        conflict = client.post(
            "/api/jobs",
            json={
                "upload_id": uploaded["upload_id"],
                "scenario_name": "existing",
                "output_folder": str(tmp_path),
                "config": config,
            },
        )
        assert conflict.status_code == 409


def test_real_trace_job_and_hdf5_playback_api(tmp_path: Path, monkeypatch) -> None:
    app = create_app(
        workspace_dir=tmp_path / "real-workspace",
        frontend_dir=tmp_path / "missing-frontend",
    )
    with TestClient(app) as client:
        uploaded = upload(client, filename="AirData-original-name.csv")
        config = client.get("/api/config/defaults").json()["config"]
        config["wind_policy"]["mode"] = "measured"
        config["engine"]["source"]["latitude"] = 28.60001
        config["engine"]["source"]["longitude"] = 77.20001
        config["engine"]["source"]["stack_height_m"] = 20.0
        config["engine"]["simulation"]["particles_per_timestep"] = 2
        config["engine"]["simulation"]["maximum_particles"] = 100
        config["playback"]["frame_interval_s"] = 1.0
        config["playback"]["visualization_parcel_limit"] = 100
        config["sensor_region"] = {"size_x_m": 18, "size_y_m": 16, "size_z_m": 8}
        created = client.post(
            "/api/jobs",
            json={
                "upload_id": uploaded["upload_id"],
                "scenario_name": "real-trace",
                "output_folder": str(tmp_path / "real-output"),
                "config": config,
            },
        )
        assert created.status_code == 200, created.text
        status = wait_for_terminal(client, created.json()["job_id"], timeout_s=15.0)
        assert status["state"] == "completed", json.dumps(status, indent=2)
        playback_id = status["result"]["playback_id"]
        assert playback_id
        metadata = client.get(f"/api/playback/{playback_id}/metadata")
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["source_csv_filename"] == "AirData-original-name.csv"
        assert metadata.json()["frame_count"] >= 2
        assert len(metadata.json()["flight_path"]) >= 2
        assert metadata.json()["source"]["x"] == metadata.json()["source"]["x_east_m"]
        assert metadata.json()["source"]["diameter_m"] == metadata.json()["source"]["stack_diameter_m"]
        frames = client.get(
            f"/api/playback/{playback_id}/frames",
            params={"start": 0, "limit": 2, "particle_limit": 100},
        )
        assert frames.status_code == 200, frames.text
        assert frames.json()["count"] == 2
        assert frames.json()["frames"][0]["drone"].keys() == {"x", "y", "z"}
        assert frames.json()["frames"][0]["sensor_region"] == {
            "size_x_m": 18.0,
            "size_y_m": 16.0,
            "size_z_m": 8.0,
        }
        assert (
            frames.json()["frames"][0]["numerical_particle_count"]
            >= frames.json()["frames"][0]["rendered_particle_count"]
        )
        samples = client.get(f"/api/playback/{playback_id}/samples?start=0")
        assert samples.status_code == 200
        assert samples.json()["total"] >= 2
        contributors = client.get(
            f"/api/playback/{playback_id}/samples/0/contributors"
        )
        assert contributors.status_code == 200, contributors.text
        assert contributors.json()["reconstructed"]["matches"] is True

        trace_path = next(
            Path(item["path"])
            for item in status["result"]["output_files"]
            if item["name"].endswith(".h5")
        )
        monkeypatch.setenv("VOXMAPS_DEFAULT_TRACE_PATH", str(trace_path))
        reloaded = client.post("/api/playback/load", json={})
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["cancelled"] is False
        assert reloaded.json()["metadata"]["simulation_id"] == metadata.json()[
            "simulation_id"
        ]
