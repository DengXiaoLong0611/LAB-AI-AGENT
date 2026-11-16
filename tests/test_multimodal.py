import base64
import io
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_lab_components import example_spec

from labagent.api import create_app


class MultimodalTests(unittest.TestCase):
    def request(self, client, text="", attachments=None):
        return client.post(
            "/api/requests/parse",
            json={
                "config": example_spec().model_dump(),
                "text": text,
                "attachments": attachments or [],
                "model": "auto",
            },
        )

    def attachment(self, name, content):
        return {"name": name, "data_base64": base64.b64encode(content).decode()}

    def test_csv_matrix_uses_physical_speeds_and_bottom_first(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(directory)) as client,
        ):
            response = self.request(
                client, attachments=[self.attachment("wind.csv", b"1,2\n3,4")]
            )
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["field_mps"], [[1, 2], [3, 4]])
            self.assertEqual(result["config"]["points"][1]["target_mps"], 2)
            self.assertEqual(client.get("/api/tasks").json(), [])

    def test_point_table_preserves_probe_coordinates(self):
        table = b"id,x_mm,y_mm,fan_x,fan_y,target_mps\na,10,20,0,1,5\nb,30,40,1,0,3"
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(directory)) as client,
        ):
            response = self.request(
                client, attachments=[self.attachment("points.csv", table)]
            )
            self.assertEqual(response.status_code, 200, response.text)
            config = response.json()["config"]
            self.assertEqual(config["points"][0]["x_mm"], 10)
            self.assertEqual(config["points"][0]["target_mps"], 5)

    def test_xlsx_matrix(self):
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append([1, 2])
        workbook.active.append([3, 4])
        stream = io.BytesIO()
        workbook.save(stream)
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(directory)) as client,
        ):
            response = self.request(
                client, attachments=[self.attachment("wind.xlsx", stream.getvalue())]
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["field_mps"], [[1, 2], [3, 4]])

    def test_image_and_text_are_sent_to_vision_and_validated(self):
        from PIL import Image

        stream = io.BytesIO()
        Image.new("RGB", (10, 10)).save(stream, "PNG")
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(directory)) as client,
        ):
            with patch("labagent.inputs.invoke_parser") as invoke:
                invoke.return_value = {
                    "summary": "均匀风场",
                    "spec": [{"type": "uniform", "value": 4}],
                }
                response = self.request(
                    client,
                    "按图生成 4 m/s",
                    [self.attachment("field.png", stream.getvalue())],
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(invoke.call_args.kwargs["vision"])
                self.assertIn("image_url", str(invoke.call_args.args[0]))
                self.assertEqual(
                    response.json()["config"]["initial_spec"],
                    [{"type": "uniform", "value": 4.0}],
                )
                invoke.return_value = {
                    "summary": "越界",
                    "spec": [{"type": "uniform", "value": 30}],
                }
                self.assertEqual(self.request(client, "风场").status_code, 422)

    def test_bad_files_and_empty_request_are_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(directory)) as client,
        ):
            self.assertEqual(self.request(client).status_code, 422)
            self.assertEqual(
                self.request(
                    client, attachments=[{"name": "a.csv", "data_base64": "???"}]
                ).status_code,
                422,
            )
            self.assertEqual(
                self.request(
                    client, attachments=[self.attachment("fake.png", b"bad")]
                ).status_code,
                422,
            )
            response = self.request(
                client, attachments=[self.attachment("wind.csv", b"1,2\n3,99")]
            )
            self.assertEqual(response.status_code, 422)

    def test_full_array_matrix_and_top_origin(self):
        from labagent.inputs import ParseRequest, parse_request
        from labagent.models import ExperimentSpec

        matrix = "\n".join(
            ",".join(str((x + y) % 20) for x in range(32)) for y in range(24)
        )
        config = ExperimentSpec(
            name="full",
            points=[dict(id="p", fan_x=0, fan_y=0, x_mm=0, y_mm=0, target_mps=1)],
        )
        request = ParseRequest(
            config=config,
            matrix_origin="top",
            attachments=[self.attachment("full.csv", matrix.encode())],
        )
        result = parse_request(request)
        self.assertEqual(len(result["spec"]), 768)
        self.assertEqual(result["field_mps"][0][:3], [3, 4, 5])
        self.assertEqual(result["field_mps"][23][:3], [0, 1, 2])

    def test_text_and_descriptive_table_use_text_model(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(create_app(directory)) as client,
        ):
            with patch("labagent.inputs.invoke_parser") as invoke:
                invoke.return_value = {
                    "summary": "渐变",
                    "spec": [{"type": "gradient", "axis": "x", "from": 2, "to": 6}],
                }
                response = self.request(
                    client,
                    "按表格说明生成",
                    [
                        self.attachment(
                            "notes.tsv", "区域\t风速\n左侧\t2\n右侧\t6".encode()
                        )
                    ],
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(invoke.call_args.kwargs["vision"])
                self.assertEqual(response.json()["field_mps"], [[2, 6], [2, 6]])

    def test_model_schema_and_spatial_errors_are_rejected(self):
        from labagent.inputs import ParseRequest, parse_request

        for spec in (
            [{"type": "raw", "matrix": [[0] * 32 for _ in range(24)]}],
            [{"type": "row", "y": 3, "value": 2}],
            [{"type": "uniform", "value": float("nan")}],
            [{"type": "uniform", "value": 2, "extra": True}],
        ):
            with (
                self.subTest(spec=spec[0]["type"]),
                patch(
                    "labagent.inputs.invoke_parser",
                    return_value={"summary": "test", "spec": spec},
                ),
            ):
                with self.assertRaises(ValueError):
                    parse_request(ParseRequest(config=example_spec(), text="测试"))

    def test_model_overrides_preserve_separate_text_and_vision_settings(self):
        from labagent.inputs import model_settings

        with (
            patch("labagent.inputs.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DEEPSEEK_API_KEY": "base-key",
                    "DEEPSEEK_MODEL": "deepseek-flash",
                    "TEXT_MODEL": "cheap",
                    "TEXT_API_KEY": "text-key",
                    "TEXT_BASE_URL": "https://text.example/v1",
                    "VISION_MODEL": "vision",
                    "VISION_API_KEY": "vision-key",
                    "VISION_BASE_URL": "https://vision.example/v1",
                },
                clear=True,
            ),
        ):
            self.assertEqual(model_settings()["model"], "cheap")
            self.assertEqual(model_settings(True)["model"], "vision")
            self.assertEqual(model_settings(True)["api_key"], "vision-key")

    def test_xls_multiple_sheets_keep_each_sheet_values(self):
        from unittest.mock import Mock

        from labagent.inputs import read_table

        first = Mock(name="first", nrows=2)
        first.name = "first"
        first.row_values.side_effect = lambda i: [[1, 2], [3, 4]][i]
        second = Mock(name="second", nrows=2)
        second.name = "second"
        second.row_values.side_effect = lambda i: [[5, 6], [7, 8]][i]
        workbook = Mock()
        workbook.sheets.return_value = [first, second]
        with patch("xlrd.open_workbook", return_value=workbook):
            tables = read_table("multi.xls", b"workbook")
        self.assertEqual(tables[0]["rows"], [[1, 2], [3, 4]])
        self.assertEqual(tables[1]["rows"], [[5, 6], [7, 8]])

    def test_dense_model_matrix_is_converted_locally(self):
        from labagent.inputs import ParseRequest, parse_request

        with patch(
            "labagent.inputs.invoke_parser",
            return_value={
                "summary": "根据附带说明读取矩阵",
                "matrix_mps": [[1, 2], [3, 4]],
            },
        ):
            result = parse_request(
                ParseRequest(
                    config=example_spec(),
                    text="请使用完整矩阵",
                    attachments=[self.attachment("field.csv", b"1,2\n3,4")],
                )
            )
        self.assertEqual(result["field_mps"], [[1, 2], [3, 4]])

    def test_returned_points_targets_follow_generated_field(self):
        from labagent.inputs import ParseRequest, parse_request

        points = [point.model_dump() for point in example_spec().points]
        with patch(
            "labagent.inputs.invoke_parser",
            return_value={
                "summary": "全场 4 m/s",
                "spec": [{"type": "uniform", "value": 4}],
                "points": points,
            },
        ):
            result = parse_request(
                ParseRequest(config=example_spec(), text="全场 4 m/s")
            )
        self.assertEqual(
            [point["target_mps"] for point in result["config"]["points"]], [4, 4]
        )
