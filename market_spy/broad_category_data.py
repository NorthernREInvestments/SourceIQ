"""Broad-category trigger keywords and predefined subcategory bucket definitions."""

# More specific categories win ties when multiple trigger sets match.
CATEGORY_PRIORITY = (
    "kitchen and cooking",
    "beauty and personal care",
    "baby and kids",
    "pet supplies",
    "outdoor and camping",
    "gaming",
    "sports",
    "automotive",
    "fashion and accessories",
    "health and wellness",
    "electronics",
    "home and garden",
)

CANONICAL_CATEGORY_LABELS = {
    "pet supplies": "Pet Supplies",
    "sports": "Sports",
    "electronics": "Electronics",
    "home and garden": "Home and Garden",
    "kitchen and cooking": "Kitchen and Cooking",
    "beauty and personal care": "Beauty and Personal Care",
    "baby and kids": "Baby and Kids",
    "automotive": "Automotive",
    "fashion and accessories": "Fashion and Accessories",
    "gaming": "Gaming",
    "outdoor and camping": "Outdoor and Camping",
    "health and wellness": "Health and Wellness",
}

# Each value is a tuple of lowercase trigger phrases/words (multi-word allowed).
BROAD_CATEGORY_TRIGGERS = {
    "pet supplies": (
        "pet", "pets", "animal", "animals", "dog", "dogs", "puppy", "puppies",
        "cat", "cats", "kitten", "kittens", "fish", "bird", "birds", "rabbit",
        "hamster", "reptile", "guinea pig", "ferret", "horse", "livestock",
        "aquarium", "terrarium", "kennel", "veterinary", "vet", "paw", "collar",
        "leash", "treat", "grooming", "litter", "cage", "crate", "pet food",
        "dog food", "cat food", "pet toy", "pet bed",
    ),
    "sports": (
        "sport", "sports", "athletic", "athletics", "fitness", "gym", "exercise",
        "workout", "training", "team", "baseball", "basketball", "football", "soccer",
        "tennis", "golf", "swimming", "cycling", "running", "hiking", "climbing",
        "skiing", "snowboard", "skateboard", "surf", "volleyball", "boxing",
        "martial arts", "yoga", "pilates", "crossfit", "weight", "dumbbell",
        "barbell", "treadmill", "outdoor sport",
    ),
    "electronics": (
        "electronic", "electronics", "tech", "technology", "gadget", "gadgets",
        "device", "devices", "phone", "smartphone", "mobile", "tablet", "laptop",
        "computer", "pc", "monitor", "keyboard", "mouse", "headphone", "speaker",
        "camera", "drone", "smartwatch", "charger", "cable", "usb", "bluetooth",
        "wifi", "router", "gaming", "console", "tv", "television", "audio",
        "smart home",
    ),
    "home and garden": (
        "home", "house", "garden", "gardening", "furniture", "decor", "decoration",
        "living room", "bedroom", "bathroom", "kitchen", "outdoor", "patio", "lawn",
        "plant", "flower", "tool", "hardware", "storage", "organizer", "cleaning",
        "candle", "lighting", "lamp", "rug", "curtain", "pillow", "bedding",
        "wall art", "frame", "vase", "interior",
    ),
    "kitchen and cooking": (
        "kitchen", "cooking", "cook", "baking", "bake", "food", "meal", "recipe",
        "utensil", "cookware", "knife", "pan", "pot", "appliance", "blender",
        "mixer", "coffee", "tea", "wine", "bar", "grill", "bbq", "cutting board",
        "bowl", "plate", "cup", "mug", "storage container",
    ),
    "beauty and personal care": (
        "beauty", "makeup", "cosmetic", "skincare", "skin care", "hair", "haircare",
        "nail", "perfume", "fragrance", "lotion", "cream", "serum", "mask", "lip",
        "eye", "foundation", "mascara", "brush", "personal care", "hygiene",
        "shampoo", "conditioner", "soap", "deodorant", "razor", "shaving",
    ),
    "baby and kids": (
        "baby", "babies", "infant", "toddler", "child", "children", "kids", "kid",
        "toy", "toys", "nursery", "stroller", "diaper", "feeding", "bottle",
        "pacifier", "educational", "learning", "play", "playground", "school",
        "backpack", "lunch box", "kids clothing", "baby clothes",
    ),
    "automotive": (
        "car", "cars", "auto", "automotive", "vehicle", "truck", "motorcycle",
        "bike", "van", "suv", "driving", "motor", "engine", "tire", "wheel",
        "accessory", "dash cam", "seat cover", "floor mat", "steering",
        "cleaning car", "detailing", "tools car", "emergency kit", "jump start",
    ),
    "fashion and accessories": (
        "fashion", "clothing", "clothes", "apparel", "wear", "shirt", "pants",
        "dress", "shoes", "sneakers", "boots", "jacket", "coat", "hat", "cap",
        "bag", "purse", "wallet", "belt", "jewelry", "necklace", "bracelet",
        "ring", "earring", "sunglasses", "watch", "accessories", "style",
        "outfit", "men fashion", "women fashion",
    ),
    "gaming": (
        "gaming", "game", "games", "gamer", "console", "pc gaming", "playstation",
        "xbox", "nintendo", "controller", "headset", "mouse pad", "gaming chair",
        "rgb", "steam", "video game", "board game", "card game", "tabletop", "rpg",
        "fps", "streaming", "twitch", "esports",
    ),
    "outdoor and camping": (
        "outdoor", "outdoors", "camping", "camp", "hiking", "hike", "backpacking",
        "survival", "adventure", "fishing", "hunt", "hunting", "kayak", "canoe",
        "rock climbing", "tent", "sleeping bag", "hammock", "fire", "flashlight",
        "lantern", "compass", "nature", "wildlife", "trail",
    ),
    "health and wellness": (
        "health", "wellness", "medical", "medicine", "supplement", "vitamin",
        "protein", "nutrition", "weight loss", "diet", "yoga", "meditation",
        "mental health", "sleep", "recovery", "massage", "therapy", "first aid",
        "pharmacy", "natural", "organic", "herbal", "essential oil", "cbd",
        "fitness health",
    ),
}

PREDEFINED_SUBCATEGORY_BUCKETS = {
    "pet supplies": [
        ("Dog Supplies", frozenset({
            "dog", "dogs", "puppy", "puppies", "canine", "leash", "collar", "harness",
            "doggie", "pooch", "retriever", "bulldog", "terrier", "kennel", "crate",
        })),
        ("Cat Supplies", frozenset({
            "cat", "cats", "kitten", "kittens", "feline", "litter", "scratching",
            "scratcher", "litterbox", "catnip",
        })),
        ("Small Animal Supplies", frozenset({
            "hamster", "rabbit", "rabbits", "guinea", "ferret", "chinchilla", "gerbil",
            "rodent", "bunny", "pig",
        })),
        ("Pet Health and Grooming", frozenset({
            "grooming", "shampoo", "flea", "tick", "health", "vitamin", "veterinary",
            "vet", "nail", "brush", "dental", "medicine", "worm", "groom", "paw",
        })),
        ("Pet Beds and Furniture", frozenset({
            "bed", "beds", "furniture", "kennel", "house", "mat", "cushion", "petbed",
            "nest", "crate",
        })),
        ("Pet Toys", frozenset({
            "toy", "toys", "chew", "ball", "fetch", "plush", "squeaky", "rope", "puzzle",
        })),
        ("Pet Food and Treats", frozenset({
            "food", "treat", "treats", "snack", "kibble", "nutrition", "feed", "biscuit",
            "jerky", "meal",
        })),
        ("Fish and Aquarium", frozenset({
            "fish", "aquarium", "tank", "filter", "aquatic", "goldfish", "betta", "pond",
            "aqua", "substrate", "terrarium",
        })),
        ("Bird Supplies", frozenset({
            "bird", "birds", "parrot", "cage", "aviary", "cockatiel", "budgie", "feeder",
            "perch",
        })),
        ("Reptile Supplies", frozenset({
            "reptile", "snake", "lizard", "gecko", "turtle", "tortoise", "hermit",
            "iguana", "habitat", "horse", "livestock",
        })),
    ],
    "sports": [
        ("Team Sports", frozenset({
            "team", "football", "soccer", "basketball", "baseball", "volleyball",
            "hockey", "lacrosse", "rugby", "cricket", "softball", "netball", "handball",
            "tennis",
        })),
        ("Fitness Equipment", frozenset({
            "gym", "fitness", "dumbbell", "kettlebell", "weight", "weights", "bench",
            "cardio", "treadmill", "elliptical", "exercise", "workout", "crossfit",
            "barbell", "resistance", "strength", "rowing", "rower", "training",
        })),
        ("Outdoor Sports", frozenset({
            "outdoor", "hiking", "climbing", "trail", "backpack", "trekking",
            "mountaineering", "archery", "adventure",
        })),
        ("Water Sports", frozenset({
            "water", "swim", "swimming", "surf", "surfing", "kayak", "kayaking",
            "paddle", "diving", "snorkel", "boat", "boating", "wetsuit", "wakeboard",
        })),
        ("Winter Sports", frozenset({
            "winter", "ski", "skiing", "snowboard", "snowboarding", "ice", "skate",
            "skating", "sled", "snowshoe", "snow",
        })),
        ("Yoga and Pilates", frozenset({
            "yoga", "pilates", "mat", "mats", "block", "strap", "meditation", "bolster",
            "stretch",
        })),
        ("Cycling", frozenset({
            "cycling", "cycle", "bike", "bicycle", "cyclist", "pedal", "helmet", "spoke",
        })),
        ("Running and Athletics", frozenset({
            "running", "runners", "runner", "jogging", "sneaker", "sneakers", "athletic",
            "athletics", "track", "sprint", "marathon", "trainers",
        })),
        ("Combat Sports", frozenset({
            "boxing", "mma", "martial", "karate", "judo", "wrestling", "combat",
            "punching", "kickboxing", "muay", "grappling",
        })),
        ("Golf", frozenset({
            "golf", "golfer", "putter", "driver", "fairway", "wedge", "tee", "caddy",
            "irons", "chip",
        })),
    ],
    "electronics": [
        ("Phone Accessories", frozenset({
            "phone", "iphone", "android", "mobile", "cellular", "smartphone", "case",
            "screen", "protector", "tablet",
        })),
        ("Laptop and Computer", frozenset({
            "laptop", "computer", "pc", "macbook", "keyboard", "mouse", "monitor",
            "desktop", "notebook", "webcam", "device",
        })),
        ("Audio and Headphones", frozenset({
            "audio", "headphone", "headphones", "earbud", "earbuds", "speaker",
            "speakers", "soundbar", "microphone", "amplifier", "bluetooth",
        })),
        ("Smart Home", frozenset({
            "smart", "alexa", "google", "hub", "automation", "thermostat", "doorbell",
            "security", "sensor", "wifi", "router",
        })),
        ("Gaming Electronics", frozenset({
            "gaming", "game", "games", "console", "playstation", "xbox", "nintendo",
            "controller", "joystick", "headset", "gamer",
        })),
        ("Cameras and Photography", frozenset({
            "camera", "cameras", "photography", "lens", "lenses", "tripod", "dslr",
            "mirrorless", "flash", "gopro", "drone",
        })),
        ("TV and Home Theater", frozenset({
            "television", "projector", "theater", "theatre", "hdmi", "receiver",
            "streaming", "roku", "firestick", "antenna", "tv",
        })),
        ("Wearables", frozenset({
            "wearable", "wearables", "smartwatch", "watch", "tracker", "fitbit", "band",
            "heart", "step",
        })),
        ("Cables and Adapters", frozenset({
            "cable", "cables", "adapter", "adapters", "usb", "connector", "dongle",
            "converter", "cord", "charger",
        })),
        ("Batteries and Power", frozenset({
            "battery", "batteries", "power", "bank", "charging", "solar", "generator",
            "inverter", "ups",
        })),
    ],
    "home and garden": [
        ("Kitchen and Dining", frozenset({
            "dining", "cookware", "utensil", "utensils", "pan", "pots", "knife",
            "cutlery", "plate", "bowl", "glassware",
        })),
        ("Bedroom and Bath", frozenset({
            "bedroom", "bath", "bathroom", "bedding", "sheet", "sheets", "pillow",
            "towel", "towels", "duvet", "comforter", "shower", "curtain",
        })),
        ("Living Room Decor", frozenset({
            "living", "decor", "decoration", "furniture", "sofa", "couch", "table",
            "rug", "rugs", "accent", "interior", "vase",
        })),
        ("Garden and Outdoor", frozenset({
            "garden", "gardening", "outdoor", "patio", "lawn", "plant", "plants",
            "planter", "greenhouse", "watering", "hose", "mower", "flower",
        })),
        ("Lighting", frozenset({
            "light", "lighting", "lamp", "lamps", "led", "bulb", "bulbs", "chandelier",
            "fixture", "sconce", "string",
        })),
        ("Storage and Organization", frozenset({
            "storage", "organization", "organizer", "organizers", "shelf", "shelves",
            "bin", "bins", "basket", "container", "closet",
        })),
        ("Cleaning Supplies", frozenset({
            "cleaning", "cleaner", "cleaners", "mop", "vacuum", "broom", "detergent",
            "disinfectant", "sponge", "wipes",
        })),
        ("Tools and Hardware", frozenset({
            "tool", "tools", "hardware", "drill", "screwdriver", "hammer", "wrench",
            "pliers", "saw", "nail", "screw",
        })),
        ("Wall Art", frozenset({
            "wall", "art", "poster", "posters", "frame", "frames", "canvas", "print",
            "painting", "picture",
        })),
        ("Candles and Fragrance", frozenset({
            "candle", "candles", "fragrance", "scent", "diffuser", "incense",
            "aromatherapy", "wax", "essential", "perfume",
        })),
    ],
    "kitchen and cooking": [
        ("Cookware", frozenset({
            "cookware", "pan", "pans", "pot", "pots", "skillet", "wok", "dutch", "cast",
        })),
        ("Bakeware", frozenset({
            "baking", "bake", "bakeware", "oven", "muffin", "cake", "cookie", "sheet",
        })),
        ("Knives and Cutlery", frozenset({
            "knife", "knives", "cutlery", "cleaver", "chef", "paring", "utensil",
        })),
        ("Small Appliances", frozenset({
            "appliance", "blender", "mixer", "toaster", "processor", "fryer", "instant",
        })),
        ("Coffee and Tea", frozenset({
            "coffee", "tea", "espresso", "kettle", "brew", "mug", "cup", "infuser",
        })),
        ("Bar and Wine", frozenset({
            "wine", "bar", "cocktail", "shaker", "decanter", "opener", "glassware",
        })),
        ("Grilling and BBQ", frozenset({
            "grill", "bbq", "barbecue", "smoker", "charcoal", "propane", "griddle",
        })),
        ("Food Storage", frozenset({
            "storage", "container", "jar", "canister", "pantry", "lid", "tupperware",
        })),
        ("Tableware", frozenset({
            "plate", "bowl", "dish", "dinnerware", "serveware", "platter", "cutting",
        })),
        ("Kitchen Utensils", frozenset({
            "spatula", "ladle", "whisk", "tongs", "peeler", "grater", "colander",
            "cooking", "cook", "recipe", "meal", "food",
        })),
    ],
    "beauty and personal care": [
        ("Skincare", frozenset({
            "skincare", "skin", "serum", "moisturizer", "cleanser", "toner", "spf",
            "cream", "lotion", "mask",
        })),
        ("Makeup", frozenset({
            "makeup", "cosmetic", "foundation", "mascara", "lipstick", "lip", "eye",
            "eyeshadow", "blush", "concealer",
        })),
        ("Hair Care", frozenset({
            "hair", "haircare", "shampoo", "conditioner", "styling", "dryer", "curl",
        })),
        ("Fragrance", frozenset({
            "perfume", "fragrance", "cologne", "scent", "parfum", "body", "mist",
        })),
        ("Nail Care", frozenset({
            "nail", "manicure", "pedicure", "polish", "lacquer",
        })),
        ("Bath and Body", frozenset({
            "bath", "body", "soap", "wash", "scrub", "lotion", "butter",
        })),
        ("Men's Grooming", frozenset({
            "shaving", "razor", "beard", "grooming", "aftershave", "trimmer",
        })),
        ("Tools and Brushes", frozenset({
            "brush", "brushes", "applicator", "sponge", "curler", "tweezer",
        })),
        ("Treatments", frozenset({
            "treatment", "peel", "retinol", "acne", "anti", "aging",
        })),
        ("Personal Hygiene", frozenset({
            "hygiene", "deodorant", "dental", "toothbrush", "floss", "personal", "care",
        })),
    ],
    "baby and kids": [
        ("Baby Gear", frozenset({
            "stroller", "carrier", "bouncer", "swing", "gear", "infant", "baby",
        })),
        ("Nursery", frozenset({
            "nursery", "crib", "bassinet", "monitor", "mobile", "changing",
        })),
        ("Diapers and Feeding", frozenset({
            "diaper", "feeding", "bottle", "pacifier", "formula", "bib", "highchair",
        })),
        ("Baby Clothing", frozenset({
            "onesie", "bodysuit", "romper", "newborn", "clothes", "baby",
        })),
        ("Toys and Play", frozenset({
            "toy", "toys", "play", "playground", "plush", "blocks", "doll",
        })),
        ("Educational", frozenset({
            "educational", "learning", "puzzle", "stem", "book", "flashcard",
        })),
        ("Kids Clothing", frozenset({
            "kids", "kid", "children", "child", "toddler", "clothing", "shirt", "dress",
        })),
        ("School Supplies", frozenset({
            "school", "backpack", "lunch", "box", "pencil", "notebook", "binder",
        })),
        ("Travel and Safety", frozenset({
            "car", "seat", "safety", "gate", "proofing", "travel",
        })),
        ("Kids Accessories", frozenset({
            "hat", "mittens", "socks", "accessories", "costume",
        })),
    ],
    "automotive": [
        ("Interior Accessories", frozenset({
            "interior", "seat", "cover", "organizer", "dash", "console", "mat",
        })),
        ("Exterior Accessories", frozenset({
            "exterior", "bumper", "spoiler", "mirror", "cover", "trim",
        })),
        ("Tools and Maintenance", frozenset({
            "tool", "tools", "wrench", "jack", "maintenance", "repair", "motor",
        })),
        ("Tires and Wheels", frozenset({
            "tire", "tires", "wheel", "wheels", "rim", "hubcap",
        })),
        ("Electronics and Dash Cams", frozenset({
            "dash", "cam", "camera", "gps", "stereo", "bluetooth", "charger",
        })),
        ("Cleaning and Detailing", frozenset({
            "cleaning", "detailing", "wax", "polish", "vacuum", "wash", "microfiber",
        })),
        ("Motorcycle", frozenset({
            "motorcycle", "motorbike", "helmet", "riding", "bike",
        })),
        ("Emergency and Safety", frozenset({
            "emergency", "kit", "jump", "start", "jumper", "safety", "first",
        })),
        ("Floor Mats and Covers", frozenset({
            "floor", "mat", "mats", "cargo", "liner", "steering",
        })),
        ("Engine and Performance", frozenset({
            "engine", "performance", "filter", "oil", "exhaust", "turbo", "driving",
        })),
    ],
    "fashion and accessories": [
        ("Women's Clothing", frozenset({
            "women", "womens", "dress", "blouse", "skirt", "leggings", "fashion",
        })),
        ("Men's Clothing", frozenset({
            "men", "mens", "shirt", "pants", "trousers", "suit", "fashion",
        })),
        ("Shoes", frozenset({
            "shoes", "sneakers", "boots", "sandals", "heels", "loafers", "footwear",
        })),
        ("Bags and Purses", frozenset({
            "bag", "bags", "purse", "handbag", "tote", "backpack", "clutch",
        })),
        ("Jewelry", frozenset({
            "jewelry", "necklace", "bracelet", "ring", "earring", "earrings", "pendant",
        })),
        ("Watches and Sunglasses", frozenset({
            "watch", "watches", "sunglasses", "eyewear", "frames",
        })),
        ("Hats and Accessories", frozenset({
            "hat", "cap", "scarf", "gloves", "belt", "accessories", "style",
        })),
        ("Dresses and Formal", frozenset({
            "dress", "formal", "gown", "evening", "cocktail", "apparel",
        })),
        ("Activewear", frozenset({
            "activewear", "athletic", "sportswear", "joggers", "hoodie", "wear",
        })),
        ("Wallets and Belts", frozenset({
            "wallet", "wallets", "belt", "belts", "cardholder", "clothing", "clothes",
        })),
    ],
    "gaming": [
        ("Console Gaming", frozenset({
            "playstation", "xbox", "nintendo", "console", "switch", "ps5", "ps4",
        })),
        ("PC Gaming", frozenset({
            "pc", "gaming", "steam", "graphics", "gpu", "keyboard", "mouse", "rgb",
        })),
        ("Controllers and Peripherals", frozenset({
            "controller", "joystick", "gamepad", "wheel", "pedal", "fight", "stick",
        })),
        ("Headsets and Audio", frozenset({
            "headset", "headphones", "microphone", "audio", "surround", "chat",
        })),
        ("Gaming Chairs", frozenset({
            "chair", "chairs", "desk", "ergonomic", "racing",
        })),
        ("Board and Card Games", frozenset({
            "board", "card", "tabletop", "deck", "dice", "puzzle",
        })),
        ("RPG and Tabletop", frozenset({
            "rpg", "dnd", "miniature", "terrain", "campaign", "tabletop",
        })),
        ("Streaming Gear", frozenset({
            "streaming", "twitch", "capture", "webcam", "green", "screen", "esports",
        })),
        ("Retro Gaming", frozenset({
            "retro", "vintage", "arcade", "emulator", "classic",
        })),
        ("Video Games", frozenset({
            "game", "games", "video", "gamer", "fps", "shooter", "adventure",
        })),
    ],
    "outdoor and camping": [
        ("Camping Gear", frozenset({
            "camping", "camp", "gear", "outdoor", "outdoors", "adventure",
        })),
        ("Hiking", frozenset({
            "hiking", "hike", "trail", "trekking", "backpacking", "backpack", "pole",
        })),
        ("Fishing", frozenset({
            "fishing", "fish", "rod", "reel", "tackle", "bait", "lure",
        })),
        ("Hunting", frozenset({
            "hunting", "hunt", "archery", "blind", "decoy", "ammo",
        })),
        ("Tents and Shelters", frozenset({
            "tent", "tents", "shelter", "canopy", "tarp", "bivy",
        })),
        ("Sleep Systems", frozenset({
            "sleeping", "bag", "pad", "mattress", "pillow", "hammock", "quilt",
        })),
        ("Outdoor Cooking", frozenset({
            "stove", "cookware", "cooler", "fire", "grill", "camp", "cooking",
        })),
        ("Navigation and Safety", frozenset({
            "compass", "gps", "flashlight", "lantern", "headlamp", "survival", "first",
        })),
        ("Climbing", frozenset({
            "climbing", "climb", "rock", "rope", "harness", "carabiner", "bouldering",
        })),
        ("Water Outdoor", frozenset({
            "kayak", "canoe", "paddle", "rafting", "water", "inflatable", "nature",
        })),
    ],
    "health and wellness": [
        ("Vitamins and Supplements", frozenset({
            "vitamin", "supplement", "mineral", "multivitamin", "capsule", "tablet",
        })),
        ("Nutrition and Protein", frozenset({
            "protein", "nutrition", "powder", "shake", "meal", "replacement",
        })),
        ("Weight Management", frozenset({
            "weight", "loss", "diet", "keto", "metabolism", "fat", "burner",
        })),
        ("Sleep and Recovery", frozenset({
            "sleep", "recovery", "melatonin", "rest", "pillow", "mattress",
        })),
        ("Massage and Therapy", frozenset({
            "massage", "therapy", "foam", "roller", "tens", "heating", "pad",
        })),
        ("First Aid", frozenset({
            "first", "aid", "bandage", "medical", "medicine", "pharmacy",
        })),
        ("Essential Oils and Natural", frozenset({
            "essential", "oil", "natural", "organic", "herbal", "aromatherapy",
        })),
        ("CBD and Herbal", frozenset({
            "cbd", "hemp", "herbal", "botanical", "extract", "tincture",
        })),
        ("Fitness Wellness", frozenset({
            "fitness", "health", "wellness", "yoga", "meditation", "exercise",
        })),
        ("Mental Wellness", frozenset({
            "mental", "stress", "anxiety", "mindfulness", "calm", "journal",
        })),
    ],
}

CATCH_ALL_NAMES = {
    "pet supplies": "General Pet Supplies",
    "sports": "General Sports",
    "electronics": "General Electronics",
    "home and garden": "General Home and Garden",
    "kitchen and cooking": "General Kitchen and Cooking",
    "beauty and personal care": "General Beauty and Personal Care",
    "baby and kids": "General Baby and Kids",
    "automotive": "General Automotive",
    "fashion and accessories": "General Fashion and Accessories",
    "gaming": "General Gaming",
    "outdoor and camping": "General Outdoor and Camping",
    "health and wellness": "General Health and Wellness",
}
