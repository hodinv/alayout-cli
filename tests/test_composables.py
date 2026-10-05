import io
import struct
import zipfile

from helpers import FakeAdb, views_responses
from typer.testing import CliRunner

from alayout import cli
from alayout.composables import (ComposableInfo, app_composables, dex_class_sources, dex_strings,
                                   list_composables, parse_source_information)

NO_INDEX = 0xFFFFFFFF


def make_dex(strings: list[str], classes: list[tuple[str, str]]) -> bytes:
    """Minimal DEX: header, string ids/data, type ids and class defs (enough for the reader)."""
    all_strings = list(strings)
    for descriptor, source in classes:
        for s in (descriptor, source):
            if s not in all_strings:
                all_strings.append(s)
    header = bytearray(0x70)
    string_ids_off = 0x70
    type_ids_off = string_ids_off + 4 * len(all_strings)
    class_defs_off = type_ids_off + 4 * len(classes)
    data_off = class_defs_off + 32 * len(classes)
    data = bytearray()
    string_offsets = []
    for s in all_strings:
        string_offsets.append(data_off + len(data))
        encoded = s.encode("utf-8")
        n = len(s)
        while True:  # uleb128 utf16 length
            byte = n & 0x7F
            n >>= 7
            data.append(byte | (0x80 if n else 0))
            if not n:
                break
        data += encoded + b"\0"
    body = bytearray()
    body += b"".join(struct.pack("<I", o) for o in string_offsets)
    body += b"".join(struct.pack("<I", all_strings.index(d)) for d, _ in classes)
    for i, (_, source) in enumerate(classes):
        body += struct.pack("<8I", i, 1, NO_INDEX, 0, all_strings.index(source), 0, 0, 0)
    struct.pack_into("<II", header, 56, len(all_strings), string_ids_off)
    struct.pack_into("<II", header, 64, len(classes), type_ids_off)
    struct.pack_into("<II", header, 96, len(classes), class_defs_off)
    return bytes(header + body + data)


APP_STRINGS = [
    "C(QuestionWithSelectionScreen)P(1)41@1863L34,60@2478L34:QuestionWithSelectionScreen.kt#oeexkd",
    "C(AnswerOption)P(2)88@3001L12:QuestionWithSelectionScreen.kt#oeexkd",
    "C(QuestionWithSelectionScreenPreview)120@5000L5:QuestionWithSelectionScreen.kt#oeexkd",
    "CC(remember):QuestionWithSelectionScreen.kt#9igjgp",
    "C59@2575L9:QuestionWithSelectionScreen.kt#oeexkd",
    "C(Text)P(14)99@1L2:Text.kt#abc",
    "plain string",
]
APP_CLASSES = [("Lcom/quitsmoke/tracker/ui/QuestionWithSelectionScreenKt;", "QuestionWithSelectionScreen.kt"),
               ("Landroidx/compose/material3/TextKt;", "Text.kt")]


def make_apk(path, strings=APP_STRINGS, classes=APP_CLASSES):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("classes.dex", make_dex(strings[:3], classes[:1]))
        z.writestr("classes2.dex", make_dex(strings[3:], classes[1:]))
        z.writestr("AndroidManifest.xml", b"binary")
    path.write_bytes(buf.getvalue())
    return path


def test_dex_reader():
    data = make_dex(["hello", "Привет"], [("La/b/C;", "C.kt")])
    assert dex_strings(data)[:2] == ["hello", "Привет"]
    assert dex_class_sources(data) == [("La/b/C;", "C.kt")]


def test_parse_source_information():
    assert parse_source_information(APP_STRINGS[0]) == ("QuestionWithSelectionScreen", "QuestionWithSelectionScreen.kt", 42)
    assert parse_source_information("C(RenderStep):Utils.kt#p4vb2x") == ("RenderStep", "Utils.kt", None)
    assert parse_source_information(APP_STRINGS[3]) is None  # inline call site
    assert parse_source_information(APP_STRINGS[4]) is None  # lambda
    assert parse_source_information("plain string") is None


def test_list_and_filter_composables(tmp_path):
    items = list_composables(make_apk(tmp_path / "app.apk"))
    assert ComposableInfo("AnswerOption", "QuestionWithSelectionScreen.kt", 89, "com.quitsmoke.tracker.ui") in items
    assert {i.name for i in items} == {"QuestionWithSelectionScreen", "AnswerOption",
                                       "QuestionWithSelectionScreenPreview", "Text"}
    assert ComposableInfo("Text", "Text.kt", 100, "androidx.compose.material3").preview is False
    app = app_composables(items, "com.quitsmoke.tracker")
    assert {i.name for i in app} == {"QuestionWithSelectionScreen", "AnswerOption", "QuestionWithSelectionScreenPreview"}
    assert {i.name for i in app_composables(items, None)} == {i.name for i in app}  # library prefixes excluded


runner = CliRunner()


def test_cli_lists_app_composables_from_local_apk(tmp_path):
    apk = make_apk(tmp_path / "app.apk")
    result = runner.invoke(cli.app, ["composables", "--apk", str(apk)])
    assert result.exit_code == 0, result.output
    assert "QuestionWithSelectionScreen.kt" in result.output
    assert "AnswerOption" in result.output and "~89" in result.output
    assert "Preview" not in result.output and "Text.kt" not in result.output
    everything = runner.invoke(cli.app, ["composables", "--apk", str(apk), "--all", "--previews"])
    assert "Text.kt" in everything.output and "QuestionWithSelectionScreenPreview" in everything.output


def test_cli_pulls_apk_of_foreground_app(tmp_path, monkeypatch):
    apk = make_apk(tmp_path / "app.apk", classes=[("Lcom/example/demo/ScreenKt;", "QuestionWithSelectionScreen.kt"),
                                                  APP_CLASSES[1]])
    responses = views_responses()
    responses["pm path com.example.demo"] = b"package:/data/app/x/base.apk\n"
    responses["cat /data/app/x/base.apk"] = apk.read_bytes()
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(responses))
    result = runner.invoke(cli.app, ["composables"])
    assert result.exit_code == 0, result.output
    assert "com.example.demo" in result.output and "AnswerOption" in result.output


def test_cli_bad_apk_path_is_an_error(tmp_path):
    result = runner.invoke(cli.app, ["composables", "--apk", str(tmp_path / "missing.apk")])
    assert result.exit_code == 1
    assert "error" in result.output


def test_prev_suffix_counts_as_preview():
    def info(name):
        return ComposableInfo(name, "A.kt", 1, "p")
    assert info("CurrencyItemPrev").preview and info("MyPlanButtonPrev2Lines").preview
    assert info("MyPlanButtonPreview").preview
    assert not info("PreviousAnswers").preview and not info("RadioItem").preview


def test_package_hash_decides_between_library_and_app_files(tmp_path):
    strings = ["C(LazyColumn)P(8)386@1L2:LazyDsl.kt#lzhash",
               "C(LazyListItem)10@1L2:LazyList.kt#lzhash",
               "C(QuestionScreen)41@1L2:QuestionScreen.kt#apphash"]
    classes = [("Landroidx/compose/foundation/lazy/LazyDslKt;", "LazyDsl.kt"),
               ("Landroidx/compose/foundation/lazy/LazyListKt;", "LazyList.kt"),
               ("Lcom/app/ui/QuestionScreenKt$inlined$1;", "LazyDsl.kt"),  # inlined code keeps the source name
               ("Lcom/app/ui/QuestionScreenKt;", "QuestionScreen.kt")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("classes.dex", make_dex(strings, classes))
    (tmp_path / "a.apk").write_bytes(buf.getvalue())
    by_name = {i.name: i.package for i in list_composables(tmp_path / "a.apk")}
    assert by_name["LazyColumn"] == "androidx.compose.foundation.lazy"
    assert by_name["QuestionScreen"] == "com.app.ui"
