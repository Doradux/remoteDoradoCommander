"""Parse the inert React bootstrap JSON without executing any frontend scripts."""
import json
import re


def page_data(html: str) -> dict:
    result = re.search(
        r"<script id='dorado-data' type='application/json'>(.*?)</script>",
        html, re.DOTALL,
    )
    assert result is not None, "Missing Dorado React bootstrap data"
    return json.loads(result.group(1))
