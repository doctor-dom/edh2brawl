from app.collection import parse_collection_csv


def test_moxfield_csv():
    csv = """Count,Name,Edition,Condition,Language,Foil,Tag
2,Sol Ring,CMR,Near Mint,English,,
1,Counterspell,DMU,Near Mint,English,,
"""
    r = parse_collection_csv(csv)
    assert r.format_detected == "moxfield"
    assert r.owned["Sol Ring"] == 2
    assert r.owned["Counterspell"] == 1


def test_goldfish_csv():
    csv = """Card,Set,Quantity
Lightning Bolt,LEA,4
"""
    r = parse_collection_csv(csv)
    assert r.format_detected == "mtggoldfish"
    assert r.owned["Lightning Bolt"] == 4
