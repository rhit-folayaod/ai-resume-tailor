from resume_tailor.matching import significant_words, term_matches


def test_c_does_not_match_cpp_or_csharp():
    assert not term_matches("C", "Built a C++ simulation")
    assert not term_matches("C", "Wrote a C# WinForms app")
    assert term_matches("C", "Wrote firmware in C for the controller")


def test_cpp_and_csharp_match_themselves():
    assert term_matches("C++", "Built a C++ simulation")
    assert term_matches("C#", "Wrote a C# WinForms app")
    assert not term_matches("C++", "Wrote a C# WinForms app")


def test_r_does_not_match_react():
    assert not term_matches("R", "Built the UI in React")
    assert term_matches("R", "Statistical analysis in R and Python")


def test_aliases_match_both_directions():
    assert term_matches("JavaScript", "wrote js modules")
    assert term_matches("JS", "wrote JavaScript modules")
    assert term_matches("PostgreSQL", "queried postgres")
    assert term_matches("Kubernetes", "deployed to k8s")


def test_matching_is_case_and_space_insensitive():
    assert term_matches("  python  ", "PYTHON developer")


def test_significant_words_drops_filler():
    assert significant_words("customer-facing solutions engineering") == {
        "customer",
        "facing",
        "solutions",
    }
