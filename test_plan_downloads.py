"""Run: python test_plan_downloads.py"""

import importlib.util

spec = importlib.util.spec_from_file_location("sd", "scribd-downloader.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

jobs, invalid = sd.plan_downloads([
    "https://www.scribd.com/document/111/Report",
    "https://www.scribd.com/document/111/Report",       # duplicate doc -> once
    "https://www.scribd.com/doc/222/report?x=1",        # name clash -> id suffix
    "https://scribd.com/document/333",                  # no slug, no www
    "https://www.scribd.com/document/444/a%3Ab%2Fc",    # unsafe chars
    "https://example.com/nope",
])

assert [j[0] for j in jobs] == ["111", "222", "333", "444"], jobs
assert [j[2] for j in jobs] == ["Report.pdf", "report-222.pdf", "333.pdf", "a_b_c.pdf"], jobs
assert invalid == ["https://example.com/nope"], invalid
print("ok")
