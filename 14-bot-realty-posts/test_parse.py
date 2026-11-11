from parse import parse, render


def test_basic_template():
    d = parse("сдаю 1к квартиру на Кремлевская 19, 5/9 эт, свежий ремонт, 50000+ку залог 20т, можно с котом")
    assert (d["rooms"], d["address"], d["price"], d["utilities"], d["deposit"], d["floor"]) == \
        ("1к квартира", "Кремлевская 19", 50000, "+ку", 20000, "5/9 эт.")
    assert render(d).splitlines()[:4] == ["1к квартира", "Кремлевская 19", "50 000+ку", "Залог 20т"]
    assert "Свежий ремонт" in d["extra"] and "можно с котом" in d["extra"]


def test_studio_and_counters():
    d = parse("Студия Баумана 58/25 25 тыс + счетчики, депозит 15000. Мебель, кондиционер")
    assert d["rooms"] == "Студия" and d["address"] == "Баумана 58/25"
    assert d["price"] == 25000 and "счётчики" in d["utilities"] and d["deposit"] == 15000
    assert "Мебель" in d["extra"]


def test_all_inclusive_and_words():
    d = parse("2 комн Пушкина 10 цена 38000 все включено залог 38 тыс без животных")
    assert d["rooms"] == "2к квартира" and d["address"] == "Пушкина 10"
    assert d["price"] == 38000 and "включено" in d["utilities"] and d["deposit"] == 38000
    assert d["extra"] == "Без животных"
