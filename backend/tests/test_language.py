from app.services.language import is_english_recommendation_eligible


def test_english_guard_rejects_romanized_hindi_titles() -> None:
    assert not is_english_recommendation_eligible("Raataan Lambiyan", "Unknown", "en")
    assert not is_english_recommendation_eligible("Tenu Sang Rakhna", "Unknown", "en")
    assert not is_english_recommendation_eligible("Satranga", "Arijit Singh", "en")
    assert not is_english_recommendation_eligible("Tum Ho Toh", "Vishal Mishra", "en")
    assert not is_english_recommendation_eligible("Tujhse.", "Pdny", "en")
    assert not is_english_recommendation_eligible("Heer", "Ali & Shjr", "en")
    assert not is_english_recommendation_eligible("Saiyaara", "Faheem Abdullah", "en")
    assert not is_english_recommendation_eligible("LOVESEXDHOKA!!!", "Chaar Diwaari", "en")
    assert not is_english_recommendation_eligible("Nanchaku", "Seedhe Maut", "en")
    assert not is_english_recommendation_eligible("So High", "Sidhu Moose Wala", "en")


def test_english_guard_uses_artist_evidence_for_ambiguous_titles() -> None:
    assert not is_english_recommendation_eligible("Chaleya", "Anirudh Ravichander & Arijit Singh", "en")
    assert not is_english_recommendation_eligible("Dhun", "Mithoon", "en")


def test_english_guard_keeps_normal_english_tracks() -> None:
    assert is_english_recommendation_eligible("There There", "Radiohead", "en")
    assert is_english_recommendation_eligible("The Chain", "Fleetwood Mac", "en")
    assert is_english_recommendation_eligible("Tumbling Dice", "The Rolling Stones", "en")
    assert is_english_recommendation_eligible("Autumn Leaves", "Frank Sinatra", "en")
    assert is_english_recommendation_eligible("Rum Tum Tugger", "Andrew Lloyd Webber", "en")
    assert is_english_recommendation_eligible("We Call It Acieeed", "D Mob", "en")
    assert not is_english_recommendation_eligible("The Chain", "Fleetwood Mac", None)
