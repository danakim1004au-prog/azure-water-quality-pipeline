"""Small reproducibility checks for the local HMI server."""

from scada_hmi import hmi_server


def test_missing_generated_ml_csv_returns_empty_frame(tmp_path):
    missing = tmp_path / "not-generated.csv"
    frame = hmi_server._read_optional_csv(
        missing,
        columns=["well_id", "predicted"],
        parse_dates=None,
    )
    assert frame.empty
    assert frame.columns.tolist() == ["well_id", "predicted"]
