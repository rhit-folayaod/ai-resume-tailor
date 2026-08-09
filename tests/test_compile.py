import pytest
from conftest import STORE_DATA

from resume_tailor.compile import TECTONIC_ENV_VAR, _summarize, compile_pdf, find_tectonic
from resume_tailor.errors import CompileError
from resume_tailor.latex import render_resume
from resume_tailor.models import ResumeStore
from resume_tailor.ranking import select


def tectonic_or_skip() -> str:
    try:
        return find_tectonic()
    except CompileError:
        pytest.skip("tectonic is not installed")


def test_missing_tectonic_explains_itself(monkeypatch, tmp_path):
    monkeypatch.delenv(TECTONIC_ENV_VAR, raising=False)
    monkeypatch.setattr("resume_tailor.compile.shutil.which", lambda _: None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CompileError, match="tectonic was not found"):
        find_tectonic()


def test_summary_extracts_the_tex_error_and_the_offending_line():
    tex = "\\documentclass{article}\n\\begin{document}\n\\nosuchmacro\n\\end{document}\n"
    output = (
        "note: this is a note\n"
        "error: Undefined control sequence.\n"
        "l.3 \\nosuchmacro\n"
        "error: the TeX engine had an unrecoverable error\n"
    )
    summary = _summarize(output, tex)
    assert "Undefined control sequence" in summary
    assert "generated line 3" in summary
    assert "nosuchmacro" in summary
    assert "unrecoverable" not in summary


def test_summary_flags_a_package_it_could_not_fetch():
    output = "error: package `nosuchpkg.sty' not found\n"
    summary = _summarize(output, "")
    assert "nosuchpkg.sty" in summary
    assert "downloads packages on first use" in summary


def test_summary_falls_back_to_the_tail_of_the_output():
    summary = _summarize("something\nunparseable happened\n", "")
    assert "unparseable happened" in summary


def test_compiles_the_real_template_to_a_pdf(backend_jd):
    binary = tectonic_or_skip()
    store = ResumeStore.model_validate(STORE_DATA)
    selection = select(store, backend_jd)
    tex = render_resume(store, selection, backend_jd)

    pdf = compile_pdf(tex, tectonic=binary)

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_broken_latex_raises_a_specific_error():
    binary = tectonic_or_skip()
    tex = "\\documentclass{article}\n\\begin{document}\n\\nosuchmacro\n\\end{document}\n"
    with pytest.raises(CompileError) as info:
        compile_pdf(tex, tectonic=binary)
    assert "Undefined control sequence" in str(info.value)


def test_compilation_leaves_no_build_files_behind(tmp_path, monkeypatch, backend_jd):
    binary = tectonic_or_skip()
    monkeypatch.chdir(tmp_path)
    store = ResumeStore.model_validate(STORE_DATA)
    selection = select(store, backend_jd)
    compile_pdf(render_resume(store, selection, backend_jd), tectonic=binary)
    assert list(tmp_path.iterdir()) == []
