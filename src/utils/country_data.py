"""Nationality-to-country map for flag asset resolution.

The flag asset class is keyed on a **country**, not on a nationality adjective, so
that one directory serves both a driver's flag and a round's (Constitution XIV.13,
"The country a flag stands for"). This module carries the correspondence.

The country names here are spelled exactly as ``tracks.country`` spells them, which
is what lets a British driver and the British Grand Prix resolve the same file. That
is why ``American`` yields ``"United States of America"`` and not ``"United States"``:
migration 029 seeds the longer form, and the two vocabularies must agree or one
country would own two flag files.

This map is **authored, not derived**. ``NATIONALITY_LOOKUP`` maps adjectives *and*
country names onto a canonical adjective, many keys to one value, and nothing in a
key says which of them was the country -- ``argentina`` is, ``argentinian`` is not.
Inverting it by rule gets ``British`` wrong (its last key is ``northern ireland``).

Usage::

    from utils.country_data import NATIONALITY_COUNTRIES
    country = NATIONALITY_COUNTRIES[nationality]   # KeyError -> module defect

A missing entry is a defect of this module and is caught by the totality test in
``tests/unit/test_country_data.py``, never by a fallback drawn at generation.
"""

from __future__ import annotations

__all__ = ["NATIONALITY_COUNTRIES", "country_for_nationality"]


def country_for_nationality(nationality: str | None) -> str | None:
    """The country whose flag stands for *nationality*.

    The single point at which a driver's recorded nationality becomes the datum the
    flag class resolves. Every graphic drawing a driver's flag calls this, so that
    five services cannot drift into five spellings.

    ``None`` in, ``None`` out: an absent nationality is not a country and seeks no
    asset at all. The caller's own rules decide whether that removes the field or
    raises a notice.

    ``"Other"`` is carried through unchanged -- it is a value, not an absence, and
    resolves ``other.svg`` by the ordinary slug rule.

    An unmapped nationality returns unchanged rather than raising. The map is total
    over the wizard's vocabulary and a test enforces it, so this cannot arise from
    anything the wizard stored; it is defence against a corrupt record, and letting
    it through to the ordinary asset-resolution path degrades to that class's
    fallback with a notice rather than killing the whole graphic mid-render.
    """
    if nationality is None:
        return None
    stripped = str(nationality).strip()
    if not stripped:
        return None
    return NATIONALITY_COUNTRIES.get(stripped, stripped)


#: Canonical nationality adjective -> the country it belongs to.
#:
#: Total over ``set(NATIONALITY_LOOKUP.values())``; the totality test enforces it.
#:
#: ``"Other"`` is recorded for a driver who stated no nationality. It is not a
#: country and gains none -- it is carried through unchanged so that such a driver
#: resolves ``other.svg`` exactly as before the class was rekeyed.
#:
#: This map now covers all 193 UN member states. Three of them share an English
#: demonym with a sibling country and would otherwise collide on one adjective:
#: ``Dominican`` (Dominica / Dominican Republic), ``Congolese`` (Congo / the
#: Democratic Republic of the Congo), and ``Guinean`` (Guinea / Guinea-Bissau).
#: Each pair's bare adjective still resolves to the country it always has --
#: ``Dominican`` to the Dominican Republic, ``Congolese`` to Congo, ``Guinean`` to
#: Guinea -- and the second country of each pair gains its own canonical adjective
#: (``Dominican (Dominica)``, ``Congolese (Kinshasa)``, ``Bissau-Guinean``) rather
#: than being silently drawn with the wrong flag.
#:
#: Taiwan is carried over even though it is not a UN member: removing an
#: already-supported nationality is a separate decision from adding the ones that
#: were missing, and nobody has asked for it.
NATIONALITY_COUNTRIES: dict[str, str] = {
    "Afghan":             "Afghanistan",
    "Albanian":           "Albania",
    "Algerian":           "Algeria",
    "American":           "United States of America",
    "Andorran":           "Andorra",
    "Angolan":            "Angola",
    "Antiguan":           "Antigua and Barbuda",
    "Argentine":          "Argentina",
    "Armenian":           "Armenia",
    "Australian":         "Australia",
    "Austrian":           "Austria",
    "Azerbaijani":        "Azerbaijan",
    "Bahamian":           "Bahamas",
    "Bahraini":           "Bahrain",
    "Bangladeshi":        "Bangladesh",
    "Barbadian":          "Barbados",
    "Basotho":            "Lesotho",
    "Belarusian":         "Belarus",
    "Belgian":            "Belgium",
    "Belizean":           "Belize",
    "Beninese":           "Benin",
    "Bhutanese":          "Bhutan",
    "Bissau-Guinean":     "Guinea-Bissau",
    "Bolivian":           "Bolivia",
    "Bosnian":            "Bosnia and Herzegovina",
    "Botswanan":          "Botswana",
    "Brazilian":          "Brazil",
    "British":            "United Kingdom",
    "Bruneian":           "Brunei",
    "Bulgarian":          "Bulgaria",
    "Burkinabe":          "Burkina Faso",
    "Burmese":            "Myanmar",
    "Burundian":          "Burundi",
    "Cambodian":          "Cambodia",
    "Cameroonian":        "Cameroon",
    "Canadian":           "Canada",
    "Cape Verdean":       "Cape Verde",
    "Central African":    "Central African Republic",
    "Chadian":            "Chad",
    "Chilean":            "Chile",
    "Chinese":            "China",
    "Colombian":          "Colombia",
    "Comorian":           "Comoros",
    "Congolese":          "Congo",
    "Congolese (Kinshasa)": "Democratic Republic of the Congo",
    "Costa Rican":        "Costa Rica",
    "Croatian":           "Croatia",
    "Cuban":              "Cuba",
    "Cypriot":            "Cyprus",
    "Czech":              "Czechia",
    "Danish":             "Denmark",
    "Djiboutian":         "Djibouti",
    "Dominican":          "Dominican Republic",
    "Dominican (Dominica)": "Dominica",
    "Dutch":              "Netherlands",
    "Ecuadorian":         "Ecuador",
    "Egyptian":           "Egypt",
    "Emirati":            "United Arab Emirates",
    "Equatorial Guinean": "Equatorial Guinea",
    "Eritrean":           "Eritrea",
    "Estonian":           "Estonia",
    "Ethiopian":          "Ethiopia",
    "Fijian":             "Fiji",
    "Filipino":           "Philippines",
    "Finnish":            "Finland",
    "French":             "France",
    "Gabonese":           "Gabon",
    "Gambian":            "Gambia",
    "Georgian":           "Georgia",
    "German":             "Germany",
    "Ghanaian":           "Ghana",
    "Greek":              "Greece",
    "Grenadian":          "Grenada",
    "Guatemalan":         "Guatemala",
    "Guinean":            "Guinea",
    "Guyanese":           "Guyana",
    "Haitian":            "Haiti",
    "Honduran":           "Honduras",
    "Hungarian":          "Hungary",
    "I-Kiribati":         "Kiribati",
    "Icelandic":          "Iceland",
    "Indian":             "India",
    "Indonesian":         "Indonesia",
    "Iranian":            "Iran",
    "Iraqi":              "Iraq",
    "Irish":              "Ireland",
    "Israeli":            "Israel",
    "Italian":            "Italy",
    "Ivorian":            "Ivory Coast",
    "Jamaican":           "Jamaica",
    "Japanese":           "Japan",
    "Jordanian":          "Jordan",
    "Kazakhstani":        "Kazakhstan",
    "Kenyan":             "Kenya",
    "Kuwaiti":            "Kuwait",
    "Kyrgyz":             "Kyrgyzstan",
    "Laotian":            "Laos",
    "Latvian":            "Latvia",
    "Lebanese":           "Lebanon",
    "Liberian":           "Liberia",
    "Libyan":             "Libya",
    "Liechtensteiner":    "Liechtenstein",
    "Lithuanian":         "Lithuania",
    "Luxembourger":       "Luxembourg",
    "Macedonian":         "North Macedonia",
    "Malagasy":           "Madagascar",
    "Malawian":           "Malawi",
    "Malaysian":          "Malaysia",
    "Maldivian":          "Maldives",
    "Malian":             "Mali",
    "Maltese":            "Malta",
    "Marshallese":        "Marshall Islands",
    "Mauritanian":        "Mauritania",
    "Mauritian":          "Mauritius",
    "Mexican":            "Mexico",
    "Micronesian":        "Micronesia",
    "Moldovan":           "Moldova",
    "Monegasque":         "Monaco",
    "Mongolian":          "Mongolia",
    "Montenegrin":        "Montenegro",
    "Moroccan":           "Morocco",
    "Mozambican":         "Mozambique",
    "Namibian":           "Namibia",
    "Nauruan":            "Nauru",
    "Nepali":             "Nepal",
    "New Zealander":      "New Zealand",
    "Ni-Vanuatu":         "Vanuatu",
    "Nicaraguan":         "Nicaragua",
    "Nigerian":           "Nigeria",
    "Nigerien":           "Niger",
    "North Korean":       "North Korea",
    "Norwegian":          "Norway",
    "Omani":              "Oman",
    "Other":              "Other",
    "Pakistani":          "Pakistan",
    "Palauan":            "Palau",
    "Panamanian":         "Panama",
    "Papua New Guinean":  "Papua New Guinea",
    "Paraguayan":         "Paraguay",
    "Peruvian":           "Peru",
    "Polish":             "Poland",
    "Portuguese":         "Portugal",
    "Qatari":             "Qatar",
    "Romanian":           "Romania",
    "Russian":            "Russia",
    "Rwandan":            "Rwanda",
    "Saint Kittsian":     "Saint Kitts and Nevis",
    "Saint Lucian":       "Saint Lucia",
    "Saint Vincentian":   "Saint Vincent and the Grenadines",
    "Salvadoran":         "El Salvador",
    "Samoan":             "Samoa",
    "San Marinese":       "San Marino",
    "Sao Tomean":         "Sao Tome and Principe",
    "Saudi":              "Saudi Arabia",
    "Senegalese":         "Senegal",
    "Serbian":            "Serbia",
    "Seychellois":        "Seychelles",
    "Sierra Leonean":     "Sierra Leone",
    "Singaporean":        "Singapore",
    "Slovak":             "Slovakia",
    "Slovenian":          "Slovenia",
    "Solomon Islander":   "Solomon Islands",
    "Somali":             "Somalia",
    "South African":      "South Africa",
    "South Korean":       "South Korea",
    "South Sudanese":     "South Sudan",
    "Spanish":            "Spain",
    "Sri Lankan":         "Sri Lanka",
    "Sudanese":           "Sudan",
    "Surinamese":         "Suriname",
    "Swazi":              "Eswatini",
    "Swedish":            "Sweden",
    "Swiss":              "Switzerland",
    "Syrian":             "Syria",
    "Taiwanese":          "Taiwan",
    "Tajik":              "Tajikistan",
    "Tanzanian":          "Tanzania",
    "Thai":               "Thailand",
    "Timorese":           "Timor-Leste",
    "Togolese":           "Togo",
    "Tongan":             "Tonga",
    "Trinidadian":        "Trinidad and Tobago",
    "Tunisian":           "Tunisia",
    "Turkish":            "Turkey",
    "Turkmen":            "Turkmenistan",
    "Tuvaluan":           "Tuvalu",
    "Ugandan":            "Uganda",
    "Ukrainian":          "Ukraine",
    "Uruguayan":          "Uruguay",
    "Uzbek":              "Uzbekistan",
    "Venezuelan":         "Venezuela",
    "Vietnamese":         "Vietnam",
    "Yemeni":             "Yemen",
    "Zambian":            "Zambia",
    "Zimbabwean":         "Zimbabwe",
}
