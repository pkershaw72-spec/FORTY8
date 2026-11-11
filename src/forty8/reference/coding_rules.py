"""Rule and lookup tables for step 4 (free-text occupancy and construction to classes),
and the labelled phrases the step 5 classifier is trained on. All synthetic.

Lookup keys are lower-case without accents. Class names map to OED codes in reference/codes.py."""

from __future__ import annotations

from forty8.reference.codes import CONSTRUCTION_CODES, OCCUPANCY_CODES
from forty8.reference.gazetteer import fold

OCCUPANCY_LOOKUP: dict[str, str] = {
    "warehouse": "warehouse", "warehousing": "warehouse", "storage": "warehouse", "bodega": "warehouse",
    "almacen": "warehouse",
    "office": "office", "offices": "office", "office building": "office", "oficinas": "office", "oficina": "office",
    "retail store": "retail", "retail": "retail", "shop": "retail", "tienda": "retail",
    "light manufacturing": "light_manufacturing", "manufactura ligera": "light_manufacturing",
    "heavy manufacturing": "heavy_manufacturing", "manufactura pesada": "heavy_manufacturing",
    "food processing": "food_processing", "procesadora de alimentos": "food_processing",
    "hotel": "hotel",
    "data centre": "data_centre", "data center": "data_centre", "centro de datos": "data_centre",
}

CONSTRUCTION_LOOKUP: dict[str, str] = {
    "wood frame": "wood", "wood": "wood", "frame": "wood", "madera": "wood",
    "masonry": "masonry", "mamposteria": "masonry",
    "reinforced concrete": "reinforced_concrete", "concrete": "reinforced_concrete",
    "concreto reforzado": "reinforced_concrete", "hormigon armado": "reinforced_concrete",
    "steel frame": "steel", "steel": "steel", "estructura de acero": "steel", "acero": "steel",
    "light metal": "light_metal", "lamina metalica": "light_metal",
}

LOOKUPS = {"occupancy": OCCUPANCY_LOOKUP, "construction": CONSTRUCTION_LOOKUP}
CODES = {"occupancy": OCCUPANCY_CODES, "construction": CONSTRUCTION_CODES}

# Training phrases for the step 5 classifier (English and Spanish).
OCCUPANCY_TRAINING: dict[str, list[str]] = {
    "warehouse": ["warehouse", "distribution center", "distribution warehouse", "logistics hub", "logistics centre",
                  "cross dock terminal", "fulfilment center", "storage depot", "freight depot", "bonded warehouse",
                  "bodega", "almacen general", "centro logistico", "centro de distribucion regional",
                  "bodega de almacenamiento", "cross-dock"],
    "office": ["office", "office building", "corporate headquarters", "head office", "administration offices",
               "admin office block", "business centre offices", "regional HQ office", "back office",
               "oficinas", "oficinas corporativas", "edificio de oficinas", "sede corporativa", "corporativo",
               "administrative building", "corporate offices"],
    "retail": ["retail store", "shopping mall", "shopping center", "department store", "supermarket",
               "retail outlet", "car showroom", "showroom and sales", "high street shop", "convenience store",
               "tienda", "tienda departamental", "centro comercial", "local comercial plaza", "supermercado",
               "sala de exhibicion"],
    "light_manufacturing": ["light manufacturing", "assembly facility", "assembly line plant", "electronics factory",
                            "electronic components assembly", "garment factory", "packaging plant", "light industrial unit",
                            "furniture workshop", "plastics moulding", "manufactura ligera", "maquiladora",
                            "planta de ensamblaje", "ensamble de componentes", "taller de manufactura",
                            "printed circuit assembly"],
    "heavy_manufacturing": ["heavy manufacturing", "steel mill", "steel fabrication", "metal foundry", "iron foundry",
                            "forging plant", "heavy engineering works", "shipyard fabrication", "smelter",
                            "casting works", "manufactura pesada", "fundicion de acero", "planta siderurgica",
                            "acereria", "fabricacion de estructuras metalicas", "rolling mill"],
    "food_processing": ["food processing", "food processing plant", "bottling facility", "beverage bottling",
                        "brewery", "dairy plant", "meat packing plant", "cold storage and packing", "bakery plant",
                        "cannery", "procesadora de alimentos", "embotelladora", "planta de bebidas",
                        "empacadora de alimentos", "planta lechera", "frozen food packing"],
    "hotel": ["hotel", "resort hotel", "beach resort", "motel", "boutique hotel", "serviced apartment hotel",
              "aparthotel", "holiday resort", "conference hotel", "guest house", "hotel de playa",
              "complejo turistico hotelero", "hotel boutique", "posada", "resort todo incluido", "inn and suites"],
    "data_centre": ["data centre", "data center", "server farm facility", "colocation data centre",
                    "colocation centre", "cloud hosting facility", "server hall", "computer centre",
                    "network operations centre", "IT hosting site", "centro de datos", "sala de servidores TI",
                    "centro de computo", "granja de servidores", "site de servidores", "hyperscale data center"],
}

CONSTRUCTION_TRAINING: dict[str, list[str]] = {
    "wood": ["wood frame", "timber frame", "timber construction", "wooden structure", "light wood frame",
             "stick frame", "stick built timber", "heavy timber", "log construction", "madera",
             "estructura de madera", "construccion de madera", "marco de madera", "plywood frame"],
    "masonry": ["masonry", "brick masonry", "brick walls", "brickwork", "concrete block masonry", "joisted masonry",
                "unreinforced masonry", "stone masonry", "block walls", "mamposteria", "tabique rojo",
                "muros de tabique", "ladrillo", "block de concreto", "masonry non-combustible"],
    "reinforced_concrete": ["reinforced concrete", "reinforced concrete frame", "RC frame structure", "concrete frame",
                            "cast in place concrete", "in-situ concrete", "precast concrete", "tilt-up concrete",
                            "concrete shear wall", "concreto reforzado", "concreto armado", "hormigon armado",
                            "estructura de concreto", "losa de concreto", "fire resistive concrete"],
    "steel": ["steel frame", "structural steel frame", "steel structure", "steel portal frame building",
              "braced steel frame", "steel moment frame", "protected steel frame", "heavy steel",
              "steel columns and beams", "estructura de acero", "acero estructural", "marco de acero",
              "estructura metalica pesada", "steel skeleton"],
    "light_metal": ["light metal", "light metal building", "pre-engineered metal", "metal clad building",
                    "metal shed", "corrugated metal sheeting", "sheet metal on light frame", "butler building",
                    "light gauge metal", "lamina metalica", "nave de lamina", "techo y muros de lamina",
                    "estructura ligera de lamina", "galvanised sheet shed"],
}

TRAINING = {"occupancy": OCCUPANCY_TRAINING, "construction": CONSTRUCTION_TRAINING}


def rule_lookup(field: str, text: str) -> str | None:
    return LOOKUPS[field].get(fold(text))


def resolve_class(field: str, value: object) -> str | None:
    """Turn an analyst's answer (class name or OED code) into a class name."""
    codes = CODES[field]
    text = fold(str(value)).replace(" ", "_")
    if text in codes:
        return text
    by_code = {str(code): label for label, code in codes.items()}
    return by_code.get(str(value).strip().split(".")[0])
