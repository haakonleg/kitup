from kitup.results import format_items


def test_format_items_prefixes_each_item():
    assert format_items(["a", "b"], sign="+") == "+a +b"
    assert format_items(["a", "b"], sign="-") == "-a -b"
    assert format_items([], sign="+") == ""
