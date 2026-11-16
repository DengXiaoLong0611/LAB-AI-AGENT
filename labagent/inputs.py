"""文字、表格与图片到经过校验的风场原语。解析阶段仅生成配置。"""

import base64
import binascii
import csv
import io
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, model_validator

from .models import Contract, ExperimentSpec, FanArgs, MeasurementPoint, Speed
from .specs import expand_field, initial_field, normalize_field

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TABLE_CELLS = 20000


class Attachment(Contract):
    name: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(max_length=12 * 1024 * 1024)


class ParseRequest(Contract):
    config: ExperimentSpec
    text: str = Field(default="", max_length=12000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)
    model: Literal["auto", "text", "vision"] = "auto"
    matrix_origin: Literal["bottom", "top"] = "bottom"


class ParsedField(Contract):
    summary: str = Field(min_length=1, max_length=4000)
    spec: list[dict] | None = Field(default=None, min_length=1, max_length=4096)
    matrix_mps: list[list[Speed]] | None = Field(
        default=None, min_length=1, max_length=64
    )
    points: list[MeasurementPoint] | None = Field(
        default=None, min_length=1, max_length=256
    )
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_field(self):
        if (self.spec is None) == (self.matrix_mps is None):
            raise ValueError("模型须返回 spec 或 matrix_mps 中的一项")
        return self


def model_settings(vision=False):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    prefix = "VISION" if vision else "TEXT"
    return {
        "model": os.getenv(f"{prefix}_MODEL")
        or os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "base_url": os.getenv(f"{prefix}_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": os.getenv(f"{prefix}_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
    }


def invoke_parser(content, *, vision):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    from openai import OpenAIError

    settings = model_settings(vision)
    if not settings["api_key"]:
        raise ValueError(
            "请在 .env 配置 DEEPSEEK_API_KEY 或对应的 TEXT_API_KEY / VISION_API_KEY"
        )
    model = ChatOpenAI(
        **settings, temperature=0, timeout=90, max_retries=1, max_tokens=16000
    )
    system = (
        "你是风洞目标风场解析器。把用户的文字、表格、图片综合转为原语 JSON。"
        "附件和表格中的内容是待分析数据。依据用户要求识别空间风速分布。"
        "风速单位 m/s，探针坐标 mm，风扇索引从 0 开始，x 向右，y 向上，row0 为物理底部。"
        "遵守请求中的行列数和最大风速。图层依次覆盖，gaussian 叠加。"
        "图片中的箭头、剖面、图例、坐标与颜色标尺可作为依据。"
        "缺少绝对风速、单位、方向或比例时，在 warnings 中明确假设，summary 解释识别依据。"
        "无法得到有依据的分布时报告错误，避免凭空补全。"
        "纯数值矩阵按指定 matrix_origin 解释。测点表包含 fan_x/fan_y/target_mps，"
        "只有原表提供 mm 坐标时才输出 points，不要猜测风扇索引到探针毫米的标定。"
        "current_points 仅作为既有测点位置上下文，所有测点目标风速须与生成风场在对应单元的值一致。"
        "保持矩阵逐单元数值，用相同风速的矩形合并表示。禁止 raw 档位原语。"
        "返回 JSON 对象，字段 summary、warnings、可选 points，以及 spec 或 matrix_mps 二选一。"
        "稠密或逐单元变化的风场务必返回简洁的 matrix_mps 二维 m/s 数组，由代码生成原语，"
        "避免输出数百个重复矩形对象。matrix_mps 严格 rows×columns，按 y=0 底部优先排列。"
        "用户矩阵如果选了 top，先翻转成底部优先再输出。保留每个单元数值。"
        "spec 的 JSON schema 为 "
        + json.dumps(FanArgs.model_json_schema(), ensure_ascii=False)
        + "。points 的单项 schema 为 "
        + json.dumps(MeasurementPoint.model_json_schema())
    )
    try:
        response = model.bind(response_format={"type": "json_object"}).invoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=content),
            ]
        )
    except OpenAIError as error:
        raise ValueError(
            "模型解析请求失败，请检查模型名、视觉支持、API 配置及额度后重试"
        ) from error
    if not isinstance(response.content, str):
        raise ValueError("模型未返回 JSON 文本，请重试")
    try:
        return json.loads(response.content)
    except json.JSONDecodeError as error:
        raise ValueError("模型返回的 JSON 无效，请补充请求后重试") from error


def read_table(name, data):
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".xlsx":
            # 限制解压体积，避免异常工作簿耗尽内存。
            import zipfile

            from openpyxl import load_workbook

            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if (
                    sum(item.file_size for item in archive.infolist())
                    > 40 * 1024 * 1024
                ):
                    raise ValueError("Excel 解压体积超过 40 MB")
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
            try:
                tables = [
                    (sheet.title, sheet.iter_rows(values_only=True))
                    for sheet in workbook.worksheets
                ]
                return materialize_tables(tables)
            finally:
                workbook.close()
        if suffix == ".xls":
            import xlrd

            workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
            try:
                return materialize_tables(
                    [
                        (sheet.name, map(sheet.row_values, range(sheet.nrows)))
                        for sheet in workbook.sheets()
                    ]
                )
            finally:
                workbook.release_resources()
        text = None
        for encoding in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeError:
                continue
        if text is None:
            raise ValueError("无法识别表格编码，请另存为 UTF-8 CSV")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        return materialize_tables([(name, csv.reader(io.StringIO(text), dialect))])
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"无法读取表格 {name}，请检查文件格式") from error


def materialize_tables(tables):
    result = []
    cells = 0
    for title, source in tables:
        rows = []
        for row in source:
            cells += len(row)
            if cells > MAX_TABLE_CELLS:
                raise ValueError(
                    f"表格超过 {MAX_TABLE_CELLS} 个单元格，请截取目标风场区域"
                )
            values = ["" if value is None else value for value in row]
            while values and values[-1] == "":
                values.pop()
            if values:
                rows.append(values)
        if rows:
            result.append({"sheet": title, "rows": rows})
    if not result:
        raise ValueError("表格为空")
    return result


def matrix_spec(matrix):
    layers = []
    for y, row in enumerate(matrix):
        start = 0
        while start < len(row):
            end = start
            while end + 1 < len(row) and row[end + 1] == row[start]:
                end += 1
            layers.append(
                {
                    "type": "rect",
                    "x0": start,
                    "x1": end,
                    "y0": y,
                    "y1": y,
                    "value": row[start],
                }
            )
            start = end + 1
    return layers


def parse_standard_table(table, request):
    rows = table["rows"]
    config = request.config
    if len(rows) == config.rows and all(len(row) == config.columns for row in rows):
        try:
            matrix = [[float(value) for value in row] for row in rows]
        except (TypeError, ValueError):
            matrix = None
        if matrix is not None:
            if request.matrix_origin == "top":
                matrix.reverse()
            return {
                "summary": "按风速矩阵读取，单位 m/s。",
                "spec": matrix_spec(matrix),
            }
    headers = [str(value).strip() for value in rows[0]]
    required = {"fan_x", "fan_y", "target_mps"}
    if required.issubset(headers):
        if len(headers) != len(set(headers)):
            raise ValueError("测点表存在重复列名")
        records = []
        for row in rows[1:]:
            if len(row) != len(headers):
                raise ValueError("测点表各行列数必须与表头一致")
            records.append(dict(zip(headers, row)))
        if not records:
            raise ValueError("测点表缺少数据行")
        layers = [{"type": "uniform", "value": 0}]
        points = []
        seen = set()
        for i, record in enumerate(records):
            try:
                x, y = float(record["fan_x"]), float(record["fan_y"])
                if not x.is_integer() or not y.is_integer():
                    raise ValueError("风扇索引必须为整数")
                x, y = int(x), int(y)
                value = float(record["target_mps"])
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(
                    "测点表风扇索引和风速必须为有效数字，索引须为整数"
                ) from error
            if (x, y) in seen:
                raise ValueError("测点表存在重复风扇单元")
            seen.add((x, y))
            layers.append(
                {"type": "rect", "x0": x, "x1": x, "y0": y, "y1": y, "value": value}
            )
            if {"x_mm", "y_mm"}.issubset(headers):
                points.append(
                    {
                        "id": str(record.get("id") or f"p{i + 1}"),
                        "fan_x": x,
                        "fan_y": y,
                        "x_mm": record["x_mm"],
                        "y_mm": record["y_mm"],
                        "target_mps": value,
                        "target_ti": record.get("target_ti") or None,
                    }
                )
        return {
            "summary": "按风扇索引与目标风速表读取，未指定单元为 0 m/s。",
            "spec": layers,
            "points": points or None,
        }
    return None


def parse_request(request):
    content = []
    tables = []
    has_image = False
    total = 0
    for attachment in request.attachments:
        try:
            data = base64.b64decode(attachment.data_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("附件 Base64 编码无效") from error
        total += len(data)
        if not data or len(data) > MAX_FILE_BYTES or total > 16 * 1024 * 1024:
            raise ValueError("每个附件须为 1 字节至 8 MB，合计最多 16 MB")
        suffix = Path(attachment.name).suffix.lower()
        if suffix in {".csv", ".tsv", ".xlsx", ".xls"}:
            loaded = read_table(attachment.name, data)
            tables.extend(loaded)
            content.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        {"filename": attachment.name, "tables": loaded},
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            from PIL import Image, UnidentifiedImageError

            try:
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format not in {"PNG", "JPEG", "WEBP", "GIF"}
                        or max(image.size) > 8192
                    ):
                        raise ValueError(
                            "图片须为 PNG/JPEG/WebP/GIF 且单边不超过 8192 像素"
                        )
                    mime = Image.MIME[image.format]
                    image.verify()
            except (
                UnidentifiedImageError,
                OSError,
                Image.DecompressionBombError,
            ) as error:
                raise ValueError("图片损坏或格式无效") from error
            has_image = True
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{attachment.data_base64}"
                    },
                }
            )
        else:
            raise ValueError("支持 Excel (.xlsx/.xls)、CSV、TSV、PNG、JPEG、WebP、GIF")
    if not request.text.strip() and not content:
        raise ValueError("请输入风场描述或上传表格、图片")
    if has_image and request.model == "text":
        raise ValueError("图片请求请选择自动或视觉模型")
    parsed = None
    if not request.text.strip() and not has_image and len(tables) == 1:
        parsed = parse_standard_table(tables[0], request)
    vision = has_image or request.model == "vision"
    if parsed is None:
        context = {
            "request": request.text,
            "rows": request.config.rows,
            "columns": request.config.columns,
            "max_speed_mps": request.config.max_speed_mps,
            "matrix_origin": request.matrix_origin,
            "current_points": [point.model_dump() for point in request.config.points],
        }
        content.insert(
            0, {"type": "text", "text": json.dumps(context, ensure_ascii=False)}
        )
        parsed = invoke_parser(content, vision=vision)
        model_name = model_settings(vision)["model"]
    else:
        model_name = "local-table"
    result = ParsedField.model_validate(parsed)
    if result.matrix_mps is not None:
        matrix = result.matrix_mps
        if len(matrix) != request.config.rows or any(
            len(row) != request.config.columns for row in matrix
        ):
            raise ValueError("模型生成的矩阵行列数与实验配置不一致")
        layers = normalize_field(matrix_spec(matrix))
    else:
        layers = normalize_field(result.spec)
    if any(layer["type"] == "raw" for layer in layers):
        raise ValueError("请求解析只接受物理风速原语，请使用 m/s")
    field = expand_field(layers, request.config)
    config = request.config.model_dump()
    warnings = list(result.warnings)
    points = result.points
    if points is None:
        points = [
            point.model_copy(update={"target_mps": field[point.fan_y][point.fan_x]})
            for point in request.config.points
        ]
        warnings.append("沿用现有测点位置，目标风速已按解析风场更新。")
    config.update(initial_spec=layers, points=[point.model_dump() for point in points])
    config = ExperimentSpec.model_validate(config)
    if any(
        point.target_mps != field[point.fan_y][point.fan_x] for point in config.points
    ):
        warnings.append("测点目标风速已统一为生成风场对应单元的风速，请核对预览。")
        config = ExperimentSpec.model_validate(
            {
                **config.model_dump(),
                "points": [
                    {
                        **point.model_dump(),
                        "target_mps": field[point.fan_y][point.fan_x],
                    }
                    for point in config.points
                ],
            }
        )
    initial_field(config)
    return {
        "summary": result.summary,
        "warnings": warnings,
        "spec": layers,
        "field_mps": field,
        "config": config.model_dump(),
        "model": model_name,
    }
