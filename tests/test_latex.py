import pytest
from conftest import STORE_DATA

from resume_tailor.errors import TemplateError
from resume_tailor.jd_parser import ParsedJobDescription
from resume_tailor.latex import escape_latex, render_resume
from resume_tailor.models import ResumeStore
from resume_tailor.ranking import SelectionBudget, select


def test_escapes_every_special_character():
    assert escape_latex("&") == r"\&"
    assert escape_latex("%") == r"\%"
    assert escape_latex("#") == r"\#"
    assert escape_latex("_") == r"\_"
    assert escape_latex("$") == r"\$"
    assert escape_latex("{") == r"\{"
    assert escape_latex("}") == r"\}"
    assert escape_latex("~") == r"\textasciitilde{}"
    assert escape_latex("^") == r"\textasciicircum{}"
    assert escape_latex("\\") == r"\textbackslash{}"


def test_cpp_and_csharp():
    assert escape_latex("C++") == "C++"
    assert escape_latex("C#") == r"C\#"
    assert escape_latex("C, C++, C#") == r"C, C++, C\#"


def test_backslash_is_not_double_escaped():
    """The classic bug: escaping '&' to '\\&' and then escaping that backslash."""

    assert escape_latex("a & b") == r"a \& b"
    assert escape_latex(r"\&") == r"\textbackslash{}\&"
    assert escape_latex("100% of {x}") == r"100\% of \{x\}"


def test_realistic_bullet_with_several_specials():
    text = "Cut costs by 40% & saved $2,600,000 using C# + C++ in module_one"
    assert escape_latex(text) == (
        r"Cut costs by 40\% \& saved \$2,600,000 using C\# + C++ in module\_one"
    )


def test_non_strings_and_none():
    assert escape_latex(None) == ""
    assert escape_latex(12) == "12"


def render(jd, budget=None, **kwargs):
    store = ResumeStore.model_validate(STORE_DATA)
    selection = select(store, jd, budget or SelectionBudget())
    return store, selection, render_resume(store, selection, jd, **kwargs)


def test_rendered_document_is_plausible_latex(backend_jd):
    _, _, tex = render(backend_jd)
    assert tex.startswith("%")
    assert r"\begin{document}" in tex
    assert tex.rstrip().endswith(r"\end{document}")
    assert tex.count(r"\begin{bullets}") == tex.count(r"\end{bullets}")


def test_rendered_document_escapes_injected_content(backend_jd):
    _, _, tex = render(backend_jd)
    assert r"C\#" in tex
    assert "C++" in tex
    assert r"cut request latency by 40\%" in tex


def test_rendered_document_contains_only_store_bullets(backend_jd):
    store, selection, tex = render(backend_jd)
    for item in selection.projects:
        for bullet in item.bullets:
            assert escape_latex(bullet.text) in tex


def test_no_unrendered_jinja_delimiters_remain(backend_jd):
    _, _, tex = render(backend_jd)
    assert r"\BLOCK{" not in tex
    assert r"\VAR{" not in tex


def test_skill_reordering_is_opt_in_and_only_reorders(backend_jd):
    jd = ParsedJobDescription(required_skills=["SQL", "Git"], role_flavor="data")
    store = ResumeStore.model_validate(STORE_DATA)
    selection = select(store, backend_jd)

    plain = render_resume(store, selection, jd)
    reordered = render_resume(store, selection, jd, reorder_skills=True)

    assert r"Python, C\#, C++, Java, SQL" in plain
    assert r"SQL, Python, C\#, C++, Java" in reordered
    assert "Git, Docker, PostgreSQL" in reordered
    for group in store.skills:
        for item in group.items:
            assert escape_latex(item) in reordered


def test_missing_template_is_reported_clearly(backend_jd):
    store = ResumeStore.model_validate(STORE_DATA)
    selection = select(store, backend_jd)
    with pytest.raises(TemplateError, match="not found"):
        render_resume(store, selection, backend_jd, template_name="nope.tex")
