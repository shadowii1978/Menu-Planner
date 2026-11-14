import flet as ft
import random
import json
import os
import inspect
import urllib.parse
import urllib.request
import re
import html
import threading
import asyncio
import time
from datetime import datetime, timedelta

# --- FPDF CHECK & IMPORT FOR PDF EXPORT ---
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

# --- WEB STORAGE & PUBLIC ASSETS DIRECTORY SETUP ---
BASE_DIR = os.path.join(os.getcwd(), "dist")
os.makedirs(BASE_DIR, exist_ok=True)

ASSETS_DIR = os.path.join(BASE_DIR, "web_assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

def cleanup_old_exports(max_age_seconds: int = 3600):
    """Cleans up generated PDF/HTML export files older than 1 hour from web_assets."""
    now = time.time()
    try:
        for fname in os.listdir(ASSETS_DIR):
            if fname.startswith(("grocery_list_", "print_menu_")):
                fpath = os.path.join(ASSETS_DIR, fname)
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_seconds:
                    os.remove(fpath)
    except Exception:
        pass

def open_url_in_browser(page: ft.Page, url: str):
    """Opens a relative asset URL or external URL in the user's web browser (Flet 0.x & 1.x compatible)."""
    if not hasattr(page, "launch_url"):
        return

    if inspect.iscoroutinefunction(page.launch_url):
        if hasattr(page, "run_task"):
            page.run_task(page.launch_url, url)
        else:
            asyncio.create_task(page.launch_url(url))
    else:
        res = page.launch_url(url)
        if inspect.isawaitable(res):
            if hasattr(page, "run_task"):
                async def _runner():
                    await res
                page.run_task(_runner)
            else:
                asyncio.create_task(res)

# --- SMART INGREDIENT NORMALIZATION & DEDUPLICATION ---
LEADING_UNITS_RE = re.compile(
    r"^(?:[\d\s/.,½⅓⅔¼¾⅛⅜⅝⅞-]+)?"
    r"(?:cups?|tbsps?|tablespoons?|tsps?|teaspoons?|oz|ounces?|lbs?|pounds?|"
    r"g|grams?|kg|ml|l|liters?|cloves?|cans?|packages?|pkgs?|slices?|pieces?|"
    r"pinch(?:es)?|dash(?:es)?|bunch(?:es)?|heads?|stalks?|sprigs?|large|medium|small|fresh|boneless|skinless)\b\s*",
    flags=re.IGNORECASE
)

INGREDIENT_ALIASES = {
    "chicken breast": "chicken",
    "chicken breasts": "chicken",
    "chicken thighs": "chicken",
    "chicken thigh": "chicken",
    "garlic clove": "garlic",
    "garlic cloves": "garlic",
    "minced garlic": "garlic",
    "yellow onion": "onion",
    "white onion": "onion",
    "red onion": "onion",
    "onions": "onion",
    "tomatoes": "tomato",
    "roma tomato": "tomato",
    "roma tomatoes": "tomato",
    "cherry tomatoes": "tomato",
    "potatoes": "potato",
    "russet potatoes": "potato",
    "eggs": "egg",
    "shredded cheddar cheese": "cheddar cheese",
    "shredded mozzarella": "mozzarella",
    "mozzarella cheese": "mozzarella",
    "parmesan cheese": "parmesan",
    "grated parmesan": "parmesan",
    "bell peppers": "bell pepper",
    "carrots": "carrot",
    "mushrooms": "mushroom",
}

def normalize_ingredient_key(raw_item: str) -> str:
    """Normalizes an ingredient string to merge duplicates across recipes."""
    s = str(raw_item).strip().lower()
    s = re.sub(r"\([^)]*\)", "", s).strip()
    s = LEADING_UNITS_RE.sub("", s).strip()
    s = LEADING_UNITS_RE.sub("", s).strip(" -–—.,")

    if s in INGREDIENT_ALIASES:
        return INGREDIENT_ALIASES[s]

    if len(s) > 4 and not s.endswith("ss"):
        if s.endswith("oes") and s[:-2] in INGREDIENT_ALIASES.values():
            s = s[:-2]
        elif s.endswith("ies"):
            s = s[:-3] + "y"
        elif s.endswith("s") and not s.endswith(("us", "is", "as")):
            singular = s[:-1]
            if singular in INGREDIENT_ALIASES:
                return INGREDIENT_ALIASES[singular]
            s = singular

    return INGREDIENT_ALIASES.get(s, s)

# --- GROCERY AISLE CATEGORIZATION ---
AISLE_ORDER = [
    "Produce",
    "Meat & Seafood",
    "Dairy & Eggs",
    "Bakery & Bread",
    "Pantry & Dry Goods"
]

AISLE_KEYWORDS = {
    "Meat & Seafood": [
        "chicken", "beef", "steak", "pork", "bacon", "sausage", "ham", "turkey",
        "pancetta", "pepperoni", "salami", "meatball", "brisket", "ribs", "fish",
        "shrimp", "salmon", "tuna", "clam", "crab", "lobster", "cod", "tilapia"
    ],
    "Produce": [
        "lettuce", "tomato", "cucumber", "broccoli", "garlic", "onion", "asparagus",
        "avocado", "spinach", "basil", "carrot", "celery", "potato", "pepper",
        "jalapeno", "mushroom", "apple", "plum", "lemon", "lime", "cilantro",
        "parsley", "ginger", "cabbage", "zucchini", "squash", "corn", "green beans",
        "scallion", "shallot", "rosemary", "thyme", "sage", "chives", "berries", "banana"
    ],
    "Dairy & Eggs": [
        "cheese", "cheddar", "mozzarella", "parmesan", "butter", "heavy cream",
        "cream", "milk", "egg", "yogurt", "sour cream", "cream cheese", "ricotta",
        "half and half", "half-and-half", "swiss", "provolone", "feta", "gouda"
    ],
    "Bakery & Bread": [
        "bun", "bread", "tortilla", "taco shell", "pizza dough", "crescent roll",
        "roll", "biscuit", "baguette", "croissant", "pita", "naan", "english muffin",
        "hoagie", "sub roll", "wrap", "bagel"
    ]
}

def categorize_ingredient_aisle(ingredient: str) -> str:
    low = ingredient.strip().lower()
    if "powder" in low or "dried" in low or "sauce" in low or "broth" in low or "paste" in low:
        return "Pantry & Dry Goods"

    for aisle, keywords in AISLE_KEYWORDS.items():
        for kw in keywords:
            if kw in low:
                return aisle
    return "Pantry & Dry Goods"

def group_grocery_entries_by_aisle(entries: list[dict]) -> dict[str, list[dict]]:
    """Groups deduplicated grocery entry dicts by store aisle."""
    grouped = {aisle: [] for aisle in AISLE_ORDER}
    for entry in sorted(entries, key=lambda x: x["name"].lower()):
        aisle = categorize_ingredient_aisle(entry["name"])
        grouped[aisle].append(entry)
    return {aisle: vals for aisle, vals in grouped.items() if vals}

def format_grocery_entry_label(entry: dict) -> str:
    """Formats display text including recipe count badge when used in 2+ meals."""
    base_label = entry["name"].capitalize()
    count = entry.get("count", 1)
    if entry.get("is_extra", False):
        return f"{base_label} (Extra)"
    if count > 1:
        return f"{base_label} (x{count} recipes)"
    return base_label

# --- RECIPES, PANTRY & DEFAULT DATA ---
DEFAULT_RECIPES = {
    "Chicken": [
        "Grilled Chicken Salad", "Chicken Stir Fry", "Garlic Butter Chicken",
        "French Onion Chicken", "Chicken Cobbler", "Slow Cooker Chicken and Gravy",
        "Crockpot Chicken Marsala", "Chicken and Mushrooms", "Chicken Marsala",
        "Cordon Bleu Chicken Rolls", "Chicken a la King", "Chick-fil-A Nuggets",
        "Chicken Noodle Casserole", "Fried Chicken", "Chicken & Waffles",
        "Chicken Alfredo", "Roasted Chicken", "Buffalo Wings", "Chicken Parmesan",
        "Chicken Pot Pie", "Chicken Spaghetti", "Million Dollar Chicken Casserole",
        "Ritz Cracker Chicken Casserole", "Chicken Cordon Bleu", "Chicken & Rice Casserole",
        "Cheesy Chicken Fritters", "Chicken Bog", "Chicken and Dumplings",
        "Honey Glazed Chicken", "Chicken Gumbo", "Stuffed Chicken Breast",
        "Buffalo Chicken Dip", "BBQ Chicken Pizza"
    ],
    "Beef": [
        "Classic Cheeseburger", "Steak and Asparagus", "Garlic Butter Steak Bites",
        "Hot Dog Chili", "Pot Roast", "Beef Stroganoff", "Meatloaf",
        "Philly Cheesesteak", "Steak and Potatoes", "Slow Cooker Brisket",
        "Grandma's Brisket", "Short Ribs", "Lemon Garlic Butter Spiedini",
        "Bacon Burger", "Mushroom Swiss Burger", "Patty Melt", "BBQ Burger",
        "Jalapeno Burger", "Meatball Stroganoff", "Big Mac", "Juicy Lucy",
        "Cheeseburger Sliders", "Salisbury Steak", "Cheeseburger Pie",
        "Meat Shell Potato Pie", "Meatball Casserole", "Cowboy Meatloaf",
        "Hamburger Potato Casserole", "Tater Tot Casserole", "Smash Burger Tacos"
    ],
    "Pork": [
        "Breaded Air Fryer Pork Chops", "Pulled Pork Sandwiches", "Pork Tenderloin",
        "Sausage and Peppers", "Carnitas Bowl", "Pork Schnitzel", "Ranch Pork Chops",
        "Ranch Pork and Potatoes", "Croque Monsieur Casserole", "Orange Glazed Pork Loin",
        "Smothered Pork Chops"
    ],
    "Pasta": [
        "Creamy Alfredo", "Spaghetti Bolognese", "Pesto Penne", "Spaghetti Carbonara",
        "Baked Ziti", "Pesto Pasta", "Clam Sauce with Linguine", "Penne Alla Vodka",
        "Lasagna", "Spaghetti and Meatballs", "Fettuccine Alfredo", "Mac and Cheese",
        "Spaghetti with Italian Sausage"
    ],
    "Mexican": [
        "Beef Tacos", "Crunch Wraps", "Mexican Tamales", "Beef Flautas",
        "Beef Burritos", "Chicken Caesar Smashed Tacos", "Chicken Flautas",
        "Chicken Enchiladas", "Chicken Fajitas", "Chicken Tacos", "Chicken Quesadillas",
        "Tamales", "Quesadillas", "Chile Rellenos", "Carne Asada Burritos",
        "Beef Enchilada Casserole", "Taquito Enchilada Casserole",
        "Shredded Beef Enchiladas", "Sour Cream Enchilada Casserole"
    ],
    "Chinese": [
        "Beef and Broccoli", "Panda Express Orange Chicken", "Sweet and Sour Pork",
        "Fried Rice", "Orange Chicken", "Lo Mein", "General Tso's Chicken",
        "Fried Dumplings", "Yakisoba Chicken", "Mongolian Chicken", "Subgum Fried Rice",
        "Beef Bulgogi", "Korean Fried Chicken", "Cream Cheese Rangoon", "Egg Rolls",
        "Sesame Chicken", "Sweet and Sour Chicken", "Cashew Chicken",
        "Teriyaki Chicken", "Shrimp Fried Rice", "Char Siu Pork", "Orange Beef",
        "Kung Pao Chicken"
    ],
    "Breakfast": [
        "German Apple Pancakes", "Breakfast Burritos", "French Toast", "Omelets",
        "Eggs Benedict", "Air Fryer French Toast Sticks", "Hash Brown and Bacon Omlet Cups",
        "Apple Cinnamon Oatmeal", "Plum Streusel Muffins", "Eggs Benedict Breakfast Pizza",
        "Crescent Roll Breakfast Pizza", "Dutch Babies", "Breakfast Sausage Casserole",
        "Biscuits and Gravy Casserole", "Egg Muffins", "Keto Breakfast Casserole",
        "Pancakes and Bacon", "Breakfast Casserole", "Cinnamon Roll Casserole",
        "Apple Pancakes", "Biscuits and Gravy", "Breakfast Sandwiches"
    ],
    "Sandwich": [
        "Pizza Meatball Subs", "Meatball Pizza Subs", "Italian Beef Sandwich",
        "Chicken Parm Grilled Cheese", "Chicken Caesar Wraps",
        "Italian Meatball Sandwich Casserole", "Turkey Club", "Reuben",
        "Italian Sub", "Tuna Melt", "Chicken Salad Sandwich"
    ],
    "Soup": [
        "Lentil Soup", "Potato Soup", "Chicken Tortilla Soup",
        "Turkey & Sausage Jambalaya", "Chicken Noodle Soup", "Pasta Fagioli Soup"
    ],
    "Seafood": [
        "Fish and Chips"
    ],
    "Vegetarian": [
        "Veggie Buddha Bowl", "Margherita Pizza", "Vegetable Curry",
        "Out to Eat", "Leftovers Night"
    ]
}

DEFAULT_INGREDIENTS_MAP = {
    "Grilled Chicken Salad": ["chicken", "lettuce", "tomato", "cucumber", "olive oil"],
    "Chicken Stir Fry": ["chicken", "soy sauce", "broccoli", "rice", "garlic"],
    "Garlic Butter Chicken": ["chicken", "butter", "garlic", "heavy cream"],
    "Classic Cheeseburger": ["ground beef", "burger buns", "cheese", "lettuce", "tomato"],
    "Beef Tacos": ["ground beef", "taco shells", "lettuce", "cheddar cheese", "salsa"],
    "Steak and Asparagus": ["steak", "asparagus", "butter", "garlic"],
    "Creamy Alfredo": ["pasta", "heavy cream", "parmesan", "butter", "garlic"],
    "Spaghetti Bolognese": ["pasta", "ground beef", "tomato sauce", "garlic"],
    "Pesto Penne": ["pasta", "pesto sauce", "parmesan", "pine nuts"],
    "Spaghetti Carbonara": ["pasta", "pancetta", "eggs", "parmesan", "black pepper"],
    "Veggie Buddha Bowl": ["rice", "quinoa", "avocado", "chickpeas", "spinach"],
    "Margherita Pizza": ["pizza dough", "mozzarella", "tomato sauce", "basil"],
    "Lentil Soup": ["lentils", "carrot", "onion", "celery", "broth"]
}

DEFAULT_MEAL_DATA = {
    "Grilled Chicken Salad": {"url": "https://www.allrecipes.com/recipe/14276/grilled-chicken-salad/"},
    "Garlic Butter Chicken": {"url": "https://www.spendwithpennies.com/garlic-butter-chicken/"},
    "Steak and Asparagus": {"url": "https://www.delish.com/cooking/recipe-ideas/a26871580/garlic-butter-steak-and-asparagus-recipe/"},
    "Creamy Alfredo": {"url": "https://www.spendwithpennies.com/fettuccine-alfredo/"},
    "Spaghetti Bolognese": {"url": "https://www.recipetineats.com/spaghetti-bolognese/"},
    "Pesto Penne": {"url": "https://www.spendwithpennies.com/easy-homemade-pesto-pasta/"},
    "Veggie Buddha Bowl": {"url": "https://www.loveandlemons.com/buddha-bowl-recipe/"},
    "Margherita Pizza": {"url": "https://www.loveandlemons.com/margherita-pizza/"},
    "Lentil Soup": {"url": "https://www.recipetineats.com/lentil-soup/"},
    "Breaded Air Fryer Pork Chops": {"url": "https://www.allrecipes.com/recipe/275553/breaded-air-fryer-pork-chops/"},
    "French Onion Chicken": {"url": "https://www.allrecipes.com/recipe/239896/crunchy-french-onion-chicken/"},
    "Pizza Meatball Subs": {"url": "https://www.thisisnotdietfood.com/air-fryer-pizza-meatball-subs/"},
    "Crunch Wraps": {"url": "https://www.spendwithpennies.com/copycat-crunchwrap-supreme/"},
    "Spaghetti Carbonara": {"url": "https://cooking.nytimes.com/recipes/12965-spaghetti-carbonara"},
    "German Apple Pancakes": {"url": "https://www.allrecipes.com/recipe/23900/german-apple-pancake/"},
    "Garlic Butter Steak Bites": {"url": "https://cooking.nytimes.com/recipes/1025322-garlic-butter-steak-bites"},
    "Chicken Cobbler": {"url": "https://www.modernhoney.com/homemade-chicken-cobbler/"},
    "Slow Cooker Chicken and Gravy": {"url": "https://coopcancook.com/crock-pot-chicken-and-gravy-recipe/"},
    "Meatball Pizza Subs": {"url": "https://tastesbetterfromscratch.com/italian-meatball-subs/"},
    "Hot Dog Chili": {"url": "https://www.tastingtable.com/821764/the-best-hot-dog-chili-recipe/"},
    "Potato Soup": {"url": "https://www.gimmesomeoven.com/potato-soup-recipe/"},
    "Pot Roast": {"url": "https://www.spendwithpennies.com/perfect-pot-roast/"},
    "Beef Stroganoff": {"url": "https://natashaskitchen.com/beef-stroganoff/"},
    "Beef Tacos": {"url": "https://lakegenevacountrymeats.com/blogs/recipes/best-ground-beef-tacos"},
    "Meatloaf": {"url": "https://www.spendwithpennies.com/the-best-classic-meatloaf/"},
    "Philly Cheesesteak": {"url": "https://www.spendwithpennies.com/philly-cheesesteaks/"},
    "Steak and Potatoes": {"url": "https://www.jocooks.com/recipes/garlic-butter-steak-and-potatoes/"},
    "Beef and Broccoli": {"url": "https://natashaskitchen.com/beef-and-broccoli-recipe/"},
    "Mexican Tamales": {"url": "https://www.maricruzavalos.com/sweet-corn-tamales-recipe/"},
    "Beef Flautas": {"url": "https://www.spendwithpennies.com/crispy-beef-flautas/"},
    "Slow Cooker Brisket": {"url": "https://www.delish.com/cooking/a26127110/slow-cooker-brisket-recipe/"},
    "Grandma's Brisket": {"url": "https://www.allrecipes.com/recipe/219176/grandmas-oven-roasted-beef-brisket/"},
    "Beef Burritos": {"url": "https://tastesbetterfromscratch.com/beef-burritos/"},
    "Short Ribs": {"url": "https://www.recipetineats.com/braised-beef-short-ribs/"},
    "Italian Beef Sandwich": {"url": "https://www.spendwithpennies.com/slow-cooker-italian-beef-sandwiches/"},
    "Breakfast Burritos": {"url": "https://tastesbetterfromscratch.com/breakfast-burritos/"},
    "French Toast": {"url": "https://www.spendwithpennies.com/classic-french-toast/"},
    "Omelets": {"url": "https://www.allrecipes.com/recipe/23868/basic-omelet/"},
    "Eggs Benedict": {"url": "https://www.spendwithpennies.com/classic-eggs-benedict/"},
    "Air Fryer French Toast Sticks": {"url": "https://www.thekitchn.com/air-fryer-french-toast-sticks-23018260"},
    "Hash Brown and Bacon Omlet Cups": {"url": "https://www.spendwithpennies.com/bacon-and-cheese-hashbrown-egg-cups/"},
    "Apple Cinnamon Oatmeal": {"url": "https://cookieandkate.com/apple-cinnamon-oatmeal-recipe/"},
    "Plum Streusel Muffins": {"url": "https://www.kingarthurbaking.com/recipes/fresh-plum-cake-or-muffins-recipe"},
    "Eggs Benedict Breakfast Pizza": {"url": "https://www.delish.com/cooking/recipe-ideas/a35639649/eggs-benedict-pizza-recipe/"},
    "Crescent Roll Breakfast Pizza": {"url": "https://www.pillsbury.com/recipes/crescent-breakfast-pizza/b3164a66-1c21-46da-b789-9a99aa2eb681"},
    "Dutch Babies": {"url": "https://www.spendwithpennies.com/dutch-baby-pancake/"},
    "Breakfast Sausage Casserole": {"url": "https://www.spendwithpennies.com/sausage-breakfast-casserole/"},
    "Biscuits and Gravy Casserole": {"url": "https://www.thecountrycook.net/biscuits-and-gravy-casserole/"},
    "Egg Muffins": {"url": "https://www.gimmesomeoven.com/baked-egg-muffins-recipe/"},
    "Keto Breakfast Casserole": {"url": "https://www.delish.com/cooking/a22569502/keto-breakfast-casserole-recipe/"},
    "Pancakes and Bacon": {"url": "https://www.allrecipes.com/recipe/21014/good-old-fashioned-pancakes/"},
    "Lemon Garlic Butter Spiedini": {"url": "https://www.allrecipes.com/recipe/242279/beef-spiedini/"},
    "Crockpot Chicken Marsala": {"url": "https://www.thecountrycook.net/slow-cooker-chicken-marsala/"},
    "Chicken and Mushrooms": {"url": "https://www.spendwithpennies.com/creamy-garlic-chicken-and-mushrooms/"},
    "Chicken Caesar Smashed Tacos": {"url": "https://www.eatwell101.com/chicken-caesar-smashed-tacos"},
    "Chicken Marsala": {"url": "https://www.delish.com/cooking/recipe-ideas/a26227092/best-chicken-marsala-recipe/"},
    "Chicken Tortilla Soup": {"url": "https://www.gimmesomeoven.com/chicken-tortilla-soup-recipe/"},
    "Turkey & Sausage Jambalaya": {"url": "https://www.spendwithpennies.com/jambalaya/"},
    "Cordon Bleu Chicken Rolls": {"url": "https://www.delish.com/cooking/recipe-ideas/a35649479/chicken-cordon-bleu-roll-ups-recipe/"},
    "Chicken Parm Grilled Cheese": {"url": "https://www.delish.com/cooking/recipe-ideas/a39556826/chicken-parm-grilled-cheese-recipe/"},
    "Chicken a la King": {"url": "https://www.spendwithpennies.com/chicken-a-la-king/"},
    "Chick-fil-A Nuggets": {"url": "https://copykat.com/chick-fil-a-nuggets/"},
    "Chicken Noodle Casserole": {"url": "https://www.thecountrycook.net/chicken-noodle-casserole/"},
    "Fried Chicken": {"url": "https://www.allrecipes.com/recipe/8758/crispy-fried-chicken/"},
    "Chicken & Waffles": {"url": "https://www.allrecipes.com/recipe/219904/chicken-and-waffles/"},
    "Panda Express Orange Chicken": {"url": "https://dinnerthendessert.com/panda-express-orange-chicken-copycat/"},
    "Chicken Alfredo": {"url": "https://www.delish.com/cooking/recipe-ideas/a26960321/best-chicken-alfredo-recipe/"},
    "Roasted Chicken": {"url": "https://www.spendwithpennies.com/perfect-roast-chicken/"},
    "Buffalo Wings": {"url": "https://www.allrecipes.com/recipe/94273/buffalo-chicken-wings/"},
    "Chicken Parmesan": {"url": "https://www.delish.com/cooking/recipe-ideas/a2022/chicken-parmesan-recipe/"},
    "Chicken Pot Pie": {"url": "https://www.spendwithpennies.com/easy-chicken-pot-pie/"},
    "Chicken Stir Fry": {"url": "https://www.gimmesomeoven.com/chicken-stir-fry/"},
    "Chicken Spaghetti": {"url": "https://tastesbetterfromscratch.com/chicken-spaghetti/"},
    "Million Dollar Chicken Casserole": {"url": "https://thecountrycook.net/million-dollar-chicken-casserole/"},
    "Ritz Cracker Chicken Casserole": {"url": "https://www.thecountrycook.net/ritz-cracker-chicken-casserole/"},
    "Chicken Cordon Bleu": {"url": "https://www.delish.com/cooking/recipe-ideas/a26960315/chicken-cordon-bleu-recipe/"},
    "Chicken & Rice Casserole": {"url": "https://www.spendwithpennies.com/creamy-chicken-and-rice-casserole/"},
    "Cheesy Chicken Fritters": {"url": "https://natashaskitchen.com/cheesy-chicken-fritters/"},
    "Chicken Bog": {"url": "https://www.allrecipes.com/recipe/238864/carolina-chicken-bog/"},
    "Chicken and Dumplings": {"url": "https://www.spendwithpennies.com/easy-chicken-and-dumplings/"},
    "Honey Glazed Chicken": {"url": "https://www.spendwithpennies.com/honey-garlic-chicken/"},
    "Chicken Gumbo": {"url": "https://www.delish.com/cooking/recipe-ideas/a35639648/chicken-and-sausage-gumbo-recipe/"},
    "Stuffed Chicken Breast": {"url": "https://www.spendwithpennies.com/spinach-stuffed-chicken-breast/"},
    "Chicken Flautas": {"url": "https://www.spendwithpennies.com/crispy-beef-flautas/"},
    "Sweet and Sour Pork": {"url": "https://www.spendwithpennies.com/sweet-and-sour-pork/"},
    "Fried Rice": {"url": "https://tastesbetterfromscratch.com/fried-rice/"},
    "Orange Chicken": {"url": "https://dinnerthendessert.com/panda-express-orange-chicken-copycat/"},
    "Lo Mein": {"url": "https://www.spendwithpennies.com/easy-chicken-lo-mein/"},
    "General Tso's Chicken": {"url": "https://natashaskitchen.com/general-tsos-chicken-recipe/"},
    "Fried Dumplings": {"url": "https://www.recipetineats.com/potstickers-dumplings/"},
    "Yakisoba Chicken": {"url": "https://www.justonecookbook.com/chicken-yakisoba/"},
    "Mongolian Chicken": {"url": "https://www.spendwithpennies.com/mongolian-chicken/"},
    "Subgum Fried Rice": {"url": "https://www.allrecipes.com/recipe/21998/subgum-fried-rice/"},
    "Beef Bulgogi": {"url": "https://www.maangchi.com/recipe/bulgogi"},
    "Korean Fried Chicken": {"url": "https://www.delish.com/cooking/recipe-ideas/a35649478/korean-fried-chicken-recipe/"},
    "Cream Cheese Rangoon": {"url": "https://www.spendwithpennies.com/crab-rangoon/"},
    "Egg Rolls": {"url": "https://www.spendwithpennies.com/homemade-egg-rolls/"},
    "Sesame Chicken": {"url": "https://www.spendwithpennies.com/sesame-chicken/"},
    "Sweet and Sour Chicken": {"url": "https://www.spendwithpennies.com/sweet-and-sour-chicken/"},
    "Chicken Enchiladas": {"url": "https://www.spendwithpennies.com/creamy-chicken-enchiladas/"},
    "Chicken Fajitas": {"url": "https://www.spendwithpennies.com/sheet-pan-chicken-fajitas/"},
    "Chicken Tacos": {"url": "https://www.spendwithpennies.com/easy-chicken-tacos/"},
    "Chicken Quesadillas": {"url": "https://www.spendwithpennies.com/chicken-quesadillas/"},
    "Buffalo Chicken Dip": {"url": "https://www.spendwithpennies.com/buffalo-chicken-dip/"},
    "BBQ Chicken Pizza": {"url": "https://www.spendwithpennies.com/barbecue-chicken-pizza/"},
    "Vegetable Curry": {"url": "https://www.spendwithpennies.com/easy-vegetable-curry/"},
    "Tater Tot Casserole": {"url": "https://www.spendwithpennies.com/tater-tot-casserole/"},
    "Smash Burger Tacos": {"url": "https://tastesbetterfromscratch.com/big-mac-smashed-tacos/"},
    "Breakfast Casserole": {"url": "https://www.spendwithpennies.com/sausage-breakfast-casserole/"},
    "Cinnamon Roll Casserole": {"url": "https://www.spendwithpennies.com/cinnamon-roll-casserole/"},
    "Chicken Caesar Wraps": {"url": "https://www.spendwithpennies.com/chicken-caesar-wrap/"},
    "Out to Eat": {"url": "https://www.allrecipes.com/"},
    "Leftovers Night": {"url": "https://www.allrecipes.com/"},
    "Cashew Chicken": {"url": "https://www.spendwithpennies.com/cashew-chicken/"},
    "Teriyaki Chicken": {"url": "https://www.spendwithpennies.com/easy-chicken-teriyaki/"},
    "Shrimp Fried Rice": {"url": "https://tastesbetterfromscratch.com/shrimp-fried-rice/"},
    "Classic Cheeseburger": {"url": "https://www.spendwithpennies.com/classic-cheeseburger/"},
    "Bacon Burger": {"url": "https://www.spendwithpennies.com/bacon-burger/"},
    "Mushroom Swiss Burger": {"url": "https://www.spendwithpennies.com/mushroom-swiss-burger/"},
    "Patty Melt": {"url": "https://www.spendwithpennies.com/classic-patty-melt/"},
    "BBQ Burger": {"url": "https://www.spendwithpennies.com/bbq-burger/"},
    "Jalapeno Burger": {"url": "https://www.spendwithpennies.com/jalapeno-burger/"},
    "Meatball Stroganoff": {"url": "https://www.spendwithpennies.com/meatball-stroganoff/"},
    "Big Mac": {"url": "https://tastesbetterfromscratch.com/big-mac-sauce-recipe/"},
    "Juicy Lucy": {"url": "https://www.delish.com/cooking/recipe-ideas/a27150130/juicy-lucy-burger-recipe/"},
    "Cheeseburger Sliders": {"url": "https://www.spendwithpennies.com/cheeseburger-sliders/"},
    "Salisbury Steak": {"url": "https://www.spendwithpennies.com/salisbury-steak/"},
    "Cheeseburger Pie": {"url": "https://www.bettycrocker.com/recipes/impossibly-easy-cheeseburger-pie/822f3e80-dabc-4f46-8d69-373e2e50587d"},
    "Meat Shell Potato Pie": {"url": "https://www.allrecipes.com/"},
    "Meatball Casserole": {"url": "https://www.spendwithpennies.com/meatball-casserole/"},
    "Italian Meatball Sandwich Casserole": {"url": "https://www.thecountrycook.net/italian-meatball-sub-casserole/"},
    "Spaghetti with Italian Sausage": {"url": "https://www.spendwithpennies.com/spaghetti-with-meat-sauce/"},
    "Cowboy Meatloaf": {"url": "https://www.spendwithpennies.com/cowboy-meatloaf/"},
    "Hamburger Potato Casserole": {"url": "https://www.spendwithpennies.com/cheesy-hamburger-potato-casserole/"},
    "Tamales": {"url": "https://www.maricruzavalos.com/sweet-corn-tamales-recipe/"},
    "Quesadillas": {"url": "https://www.spendwithpennies.com/cheese-quesadilla/"},
    "Chile Rellenos": {"url": "https://www.delish.com/cooking/recipe-ideas/a35639651/chiles-rellenos-recipe/"},
    "Carne Asada Burritos": {"url": "https://tastesbetterfromscratch.com/carne-asada-burritos/"},
    "Beef Enchilada Casserole": {"url": "https://www.spendwithpennies.com/beef-enchilada-casserole/"},
    "Taquito Enchilada Casserole": {"url": "https://www.thecountrycook.net/chicken-taquito-casserole/"},
    "Shredded Beef Enchiladas": {"url": "https://www.spendwithpennies.com/shredded-beef-enchiladas/"},
    "Sour Cream Enchilada Casserole": {"url": "https://www.spendwithpennies.com/sour-cream-chicken-enchiladas/"},
    "Baked Ziti": {"url": "https://www.spendwithpennies.com/baked-ziti/"},
    "Pesto Pasta": {"url": "https://www.spendwithpennies.com/easy-homemade-pesto-pasta/"},
    "Clam Sauce with Linguine": {"url": "https://www.delish.com/cooking/recipe-ideas/a35649477/linguine-with-clam-sauce-recipe/"},
    "Penne Alla Vodka": {"url": "https://www.spendwithpennies.com/penne-alla-vodka/"},
    "Pulled Pork Sandwiches": {"url": "https://www.spendwithpennies.com/slow-cooker-pulled-pork/"},
    "Pork Tenderloin": {"url": "https://www.spendwithpennies.com/roasted-pork-tenderloin/"},
    "Sausage and Peppers": {"url": "https://www.spendwithpennies.com/sheet-pan-sausage-and-peppers/"},
    "Carnitas Bowl": {"url": "https://tastesbetterfromscratch.com/pork-carnitas/"},
    "Pork Schnitzel": {"url": "https://www.spendwithpennies.com/pork-schnitzel/"},
    "Ranch Pork Chops": {"url": "https://www.spendwithpennies.com/ranch-pork-chops/"},
    "Ranch Pork and Potatoes": {"url": "https://www.thecountrycook.net/ranch-pork-chops-and-potatoes/"},
    "Croque Monsieur Casserole": {"url": "https://www.delish.com/cooking/recipe-ideas/a35639650/croque-monsieur-casserole-recipe/"},
    "Orange Glazed Pork Loin": {"url": "https://www.spendwithpennies.com/orange-glazed-pork-loin/"},
    "Char Siu Pork": {"url": "https://www.recipetineats.com/chinese-barbecue-pork-char-siu/"},
    "Lasagna": {"url": "https://www.spendwithpennies.com/classic-homemade-lasagna/"},
    "Spaghetti and Meatballs": {"url": "https://www.spendwithpennies.com/spaghetti-and-meatballs/"},
    "Fettuccine Alfredo": {"url": "https://www.spendwithpennies.com/fettuccine-alfredo/"},
    "Mac and Cheese": {"url": "https://www.spendwithpennies.com/stovetop-mac-and-cheese/"},
    "Turkey Club": {"url": "https://www.spendwithpennies.com/classic-turkey-club-sandwich/"},
    "Reuben": {"url": "https://www.spendwithpennies.com/classic-reuben-sandwich/"},
    "Italian Sub": {"url": "https://www.spendwithpennies.com/ultimate-italian-sub-sandwich/"},
    "Smothered Pork Chops": {"url": "https://www.spendwithpennies.com/smothered-pork-chops/"},
    "Tuna Melt": {"url": "https://www.spendwithpennies.com/tuna-melt/"},
    "Chicken Salad Sandwich": {"url": "https://www.spendwithpennies.com/classic-chicken-salad/"},
    "Chicken Noodle Soup": {"url": "https://www.spendwithpennies.com/homemade-chicken-noodle-soup/"},
    "Pasta Fagioli Soup": {"url": "https://www.spendwithpennies.com/pasta-fagioli-soup/"},
    "Fish and Chips": {"url": "https://www.spendwithpennies.com/crispy-beer-battered-fish-and-chips/"},
    "Orange Beef": {"url": "https://tastesbetterfromscratch.com/orange-beef/"},
    "Kung Pao Chicken": {"url": "https://www.spendwithpennies.com/kung-pao-chicken/"},
    "Apple Pancakes": {"url": "https://www.spendwithpennies.com/apple-pancakes/"},
    "Biscuits and Gravy": {"url": "https://www.spendwithpennies.com/biscuits-and-gravy/"},
    "Breakfast Sandwiches": {"url": "https://www.spendwithpennies.com/easy-breakfast-sandwich/"}
}

DEFAULT_INGREDIENTS = ["salt", "pepper", "olive oil"]
DEFAULT_TIMERS = [
    {"title": "Main Dish Timer", "total_seconds": 900, "remaining": 900, "running": False},
    {"title": "Oven Bake Timer", "total_seconds": 1800, "remaining": 1800, "running": False}
]

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
TODAY_IDX = (datetime.today().weekday() + 1) % 7

CATEGORY_CONFIG = {
    "Chicken": {"color": "#E65100", "emoji": "🍗"},
    "Beef": {"color": "#B71C1C", "emoji": "🥩"},
    "Pork": {"color": "#880E4F", "emoji": "🥓"},
    "Pasta": {"color": "#F57F17", "emoji": "🍝"},
    "Soup": {"color": "#006064", "emoji": "🍲"},
    "Vegetarian": {"color": "#2E7D32", "emoji": "🥗"},
    "Seafood": {"color": "#01579B", "emoji": "🦐"},
    "Mexican": {"color": "#D84315", "emoji": "🌮"},
    "Italian": {"color": "#C2185B", "emoji": "🍕"},
    "Chinese": {"color": "#D84315", "emoji": "🥡"},
    "Breakfast": {"color": "#FF8F00", "emoji": "🥞"},
    "Ground beef": {"color": "#C62828", "emoji": "🥩"},
    "Sandwich": {"color": "#EF6C00", "emoji": "🥪"},
    "Soup/salad": {"color": "#2E7D32", "emoji": "🥗"}
}
DEFAULT_CAT_CONFIG = {"color": "#5D00BA", "emoji": "🍽️"}

def load_json_file(filepath, default_val):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except:
            pass
    return default_val

def save_json_file(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def get_week_key():
    today = datetime.today()
    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday)
    return sunday.strftime("%Y-%m-%d")

def get_all_recipe_pairs(recipes_dict):
    source = recipes_dict if any(recipes_dict.values()) else DEFAULT_RECIPES
    return [
        {"category": cat, "recipe": r}
        for cat, rec_list in source.items()
        for r in rec_list
    ]

def generate_balanced_week(recipes_dict, existing_week=None):
    all_pairs = get_all_recipe_pairs(recipes_dict)
    if not all_pairs:
        return [{"category": "General", "recipe": "Leftovers Night", "locked": False} for _ in range(7)]

    week = [None] * 7
    used_recipes = set()

    if existing_week and len(existing_week) == 7:
        for i, item in enumerate(existing_week):
            if isinstance(item, dict) and item.get("locked", False):
                locked_entry = dict(item)
                locked_entry["locked"] = True
                week[i] = locked_entry
                if locked_entry.get("recipe"):
                    used_recipes.add(locked_entry["recipe"])

    for i in range(7):
        if week[i] is not None:
            continue

        adjacent_cats = set()
        if i > 0 and week[i - 1] is not None:
            adjacent_cats.add(week[i - 1].get("category"))
        if i < 6 and week[i + 1] is not None:
            adjacent_cats.add(week[i + 1].get("category"))

        candidates = [
            p for p in all_pairs
            if p["recipe"] not in used_recipes and p["category"] not in adjacent_cats
        ]
        if not candidates:
            candidates = [p for p in all_pairs if p["recipe"] not in used_recipes]
        if not candidates:
            candidates = all_pairs

        chosen = dict(random.choice(candidates))
        chosen["locked"] = False
        week[i] = chosen
        used_recipes.add(chosen["recipe"])

    return week

def fetch_ingredients_from_url(url: str) -> list[str]:
    """Extracts and cleans recipe ingredients from Schema.org JSON-LD metadata."""
    if not url or not url.startswith(("http://", "https://")):
        return []

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw_html = resp.read().decode("utf-8", errors="ignore")

    scripts = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    def find_recipe_node(node):
        if isinstance(node, dict):
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if any(str(t).lower() == "recipe" for t in types):
                return node
            for val in node.values():
                found = find_recipe_node(val)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = find_recipe_node(item)
                if found:
                    return found
        return None

    raw_ingredients = []
    for block in scripts:
        try:
            data = json.loads(block.strip())
            recipe_node = find_recipe_node(data)
            if recipe_node and "recipeIngredient" in recipe_node:
                raw_ingredients = recipe_node["recipeIngredient"]
                break
        except Exception:
            continue

    cleaned = []
    for item in raw_ingredients:
        text = html.unescape(str(item)).strip()
        text = re.sub(r"\([^)]*\)", "", text)
        text = text.split(",")[0]
        text = LEADING_UNITS_RE.sub("", text).strip(" -–—.")
        if text and text.lower() not in [c.lower() for c in cleaned]:
            cleaned.append(text.lower())

    return cleaned

def main(page: ft.Page):
    WEEK_FILE = os.path.join(BASE_DIR, "current_week.json")
    RECIPE_FILE = os.path.join(BASE_DIR, "recipes.json")
    PANTRY_FILE = os.path.join(BASE_DIR, "pantry.json")
    EXTRA_GROCERY_FILE = os.path.join(BASE_DIR, "extra_grocery_items.json")
    SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
    TIMERS_FILE = os.path.join(BASE_DIR, "timers.json")
    INGREDIENTS_FILE = os.path.join(BASE_DIR, "recipe_ingredients.json")
    MEAL_DATA_FILE = os.path.join(BASE_DIR, "meal_data.json")

    settings = load_json_file(SETTINGS_FILE, {"theme_mode": "dark"})
    saved_theme = settings.get("theme_mode", "dark")
    
    page.title = "Meal Planner Suite (Web)"
    page.theme_mode = ft.ThemeMode.DARK if saved_theme == "dark" else ft.ThemeMode.LIGHT
    page.padding = 15
    page.vertical_alignment = ft.MainAxisAlignment.START

    # --- 3-SECOND ASYNC-SAFE VISUAL DEBUG BANNER ---
    debug_banner_text = ft.Text("", color="white", weight=ft.FontWeight.BOLD, size=14)
    debug_banner = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.CHECK_CIRCLE, color="white", size=20),
            debug_banner_text
        ], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREEN_700,
        padding=12,
        border_radius=8,
        visible=False
    )

    banner_seq = [0]

    async def _hide_banner_after(seq_id: int, seconds: int):
        await asyncio.sleep(seconds)
        if banner_seq[0] == seq_id:
            debug_banner.visible = False
            page.update()

    def show_debug_banner(message: str, is_error: bool = False, seconds: int = 3):
        banner_seq[0] += 1
        current_seq = banner_seq[0]
        debug_banner_text.value = message
        debug_banner.bgcolor = ft.Colors.RED_700 if is_error else ft.Colors.GREEN_700
        debug_banner.visible = True
        page.update()

        if hasattr(page, "run_task"):
            page.run_task(_hide_banner_after, current_seq, seconds)
        else:
            def fallback_hide():
                time.sleep(seconds)
                if banner_seq[0] == current_seq:
                    debug_banner.visible = False
                    try:
                        page.update()
                    except Exception:
                        pass
            threading.Thread(target=fallback_hide, daemon=True).start()

    saved_recipes = load_json_file(RECIPE_FILE, {})
    recipes = {}
    existing_names = {r.strip().lower() for rec_list in saved_recipes.values() for r in rec_list}
    for cat, rec_list in saved_recipes.items():
        recipes[cat] = list(rec_list)
    for cat, default_list in DEFAULT_RECIPES.items():
        if cat not in recipes:
            recipes[cat] = []
        for r in default_list:
            if r.strip().lower() not in existing_names:
                recipes[cat].append(r)
                existing_names.add(r.strip().lower())
    save_json_file(RECIPE_FILE, recipes)
    
    default_pantry_items = [
        "salt", "milk", "black pepper", "olive oil", "vegetable oil",
        "all-purpose flour", "sugar", "garlic powder", "onion powder",
        "baking powder", "baking soda", "cinnamon", "Butter", "vanilla extract",
        "Paprika", "Cayenne Pepper", "chili powder", "dried oregano",
        "dried basil", "dried thyme", "dried rosemary", "dried parsley",
        "dried sage", "Cumin", "Red Pepper Flakes", "Corn Starch",
        "Brown Sugar", "Honey", "Soy Sauce", "Worcestershire Sauce",
        "Hot Sauce", "White Vinegar", "Long Grain White Rice"
    ]
    
    saved_pantry = load_json_file(PANTRY_FILE, [])
    pantry = list(dict.fromkeys(saved_pantry + default_pantry_items))
    save_json_file(PANTRY_FILE, pantry)

    extra_grocery_items = load_json_file(EXTRA_GROCERY_FILE, [])
    
    recently_checked_off = []
    selected_swap_idx = [None]
    previous_week = []
    plain_text_mode = [False]
    
    timers_list = load_json_file(TIMERS_FILE, DEFAULT_TIMERS)
    saved_ingredients = load_json_file(INGREDIENTS_FILE, {})
    recipe_ingredients = {**DEFAULT_INGREDIENTS_MAP, **saved_ingredients}
    
    saved_meal_data = load_json_file(MEAL_DATA_FILE, {})
    meal_data = {**DEFAULT_MEAL_DATA, **saved_meal_data}
    
    def get_recipe_url(name):
        if not name:
            return ""
        if name in meal_data:
            entry = meal_data[name]
            url = entry.get("url", "") if isinstance(entry, dict) else str(entry)
            if url:
                return url
        clean_name = name.strip().lower()
        for k, v in meal_data.items():
            if k.strip().lower() == clean_name:
                url = v.get("url", "") if isinstance(v, dict) else str(v)
                if url:
                    return url
        query = urllib.parse.quote_plus(name.strip())
        return f"https://www.allrecipes.com/search?q={query}"

    week_data = load_json_file(WEEK_FILE, {})
    if week_data.get("week_key") == get_week_key() and len(week_data.get("week", [])) == 7:
        current_week = week_data.get("week", [])
        for m in current_week:
            if isinstance(m, dict) and "locked" not in m:
                m["locked"] = False
    else:
        current_week = generate_balanced_week(recipes)
        save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})

    content_area = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)

    def open_dialog_safe(dlg):
        if hasattr(page, "open"):
            page.open(dlg)
        else:
            page.dialog = dlg
            dlg.open = True
            page.update()

    def close_dialog_safe(dlg):
        if hasattr(page, "close"):
            page.close(dlg)
        else:
            dlg.open = False
            page.update()

    def show_snackbar(message, is_error=False, duration_ms=4000):
        try:
            sb = ft.SnackBar(
                content=ft.Text(message, color="white"),
                bgcolor=ft.Colors.RED_700 if is_error else ft.Colors.GREEN_700,
                duration=duration_ms
            )
            if hasattr(page, "open"):
                page.open(sb)
            else:
                page.snack_bar = sb
                sb.open = True
                page.update()
        except:
            pass

    def toggle_theme(e):
        if page.theme_mode == ft.ThemeMode.DARK:
            page.theme_mode = ft.ThemeMode.LIGHT
            theme_btn_text.value = "🌙 Dark Mode"
            new_mode = "light"
        else:
            page.theme_mode = ft.ThemeMode.DARK
            theme_btn_text.value = "☀️ Light Mode"
            new_mode = "dark"
        
        save_json_file(SETTINGS_FILE, {"theme_mode": new_mode})
        page.update()

    theme_btn_text = ft.Text("🌙 Dark Mode" if page.theme_mode == ft.ThemeMode.LIGHT else "☀️ Light Mode")
    theme_container = ft.Container(
        content=theme_btn_text,
        padding=8,
        ink=True,
        border_radius=5,
        on_click=toggle_theme
    )

    page.appbar = ft.AppBar(
        title=ft.Text("🍽️ Meal Planner Suite", weight=ft.FontWeight.BOLD),
        bgcolor=ft.Colors.DEEP_PURPLE_900,
        center_title=False,
        actions=[theme_container]
    )

    def show_recipe_details(recipe_name, category, is_editing=False):
        content_area.controls.clear()
        c_info = CATEGORY_CONFIG.get(category, DEFAULT_CAT_CONFIG)
        ingredients = recipe_ingredients.get(recipe_name, DEFAULT_INGREDIENTS)
        recipe_url = get_recipe_url(recipe_name)

        top_bar = ft.Row([
            ft.TextButton("← Back to Recipes", on_click=lambda e: show_recipes()),
            ft.TextButton(
                "Cancel Edit" if is_editing else "✏️ Edit Recipe",
                on_click=lambda e: show_recipe_details(recipe_name, category, is_editing=not is_editing)
            )
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        if not is_editing:
            details_controls = [
                ft.Text(f"{c_info['emoji']} {recipe_name}", size=24, weight=ft.FontWeight.BOLD),
                ft.Text(f"Category: {category}", italic=True, color="grey_400"),
                ft.Divider(),
                ft.Text("🛒 Required Ingredients:", weight=ft.FontWeight.BOLD, size=16),
                ft.Column([ft.Text(f"• {ing.capitalize()}") for ing in ingredients]),
                ft.Divider(),
                ft.Text("📝 Instructions:", weight=ft.FontWeight.BOLD, size=16),
                ft.Text(f"1. Prepare all ingredients for {recipe_name.lower()}.\n2. Cook according to standard instructions.\n3. Serve warm and enjoy!")
            ]

            link_content = ft.Row([
                ft.Icon(ft.Icons.LINK, color="amber_400", size=18),
                ft.Text(recipe_url, color="amber_300", expand=True, overflow=ft.TextOverflow.ELLIPSIS)
            ])

            link_container = ft.Container(
                content=link_content,
                padding=10,
                bgcolor=ft.Colors.DEEP_PURPLE_900,
                border_radius=8,
                ink=True,
                url=recipe_url
            )

            details_controls.extend([
                ft.Divider(),
                ft.Text("🌐 Recipe Source Link (Tap to launch):", weight=ft.FontWeight.BOLD, size=16),
                link_container
            ])

            day_dropdown = ft.Dropdown(
                label="Select Day",
                value=DAYS[TODAY_IDX],
                options=[ft.dropdown.Option(d) for d in DAYS],
                expand=True,
                border_radius=8
            )

            def assign_to_schedule(e):
                selected_day_name = day_dropdown.value
                try:
                    day_idx = DAYS.index(selected_day_name)
                except ValueError:
                    day_idx = 0

                while len(current_week) < 7:
                    current_week.append({"category": "Chicken", "recipe": "Leftovers Night", "locked": False})

                keep_lock = current_week[day_idx].get("locked", False)
                current_week[day_idx] = {"category": category, "recipe": recipe_name, "locked": keep_lock}
                save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
                show_snackbar(f"📅 Assigned '{recipe_name}' to {selected_day_name}!")

            assign_btn = ft.Container(
                content=ft.Text("Assign to Schedule", color="amber_300", weight=ft.FontWeight.BOLD),
                padding=12,
                bgcolor=ft.Colors.DEEP_PURPLE_700,
                border_radius=8,
                ink=True,
                on_click=assign_to_schedule
            )

            assignment_section = ft.Container(
                content=ft.Column([
                    ft.Divider(),
                    ft.Text("📅 Schedule Assignment", weight=ft.FontWeight.BOLD, size=16, color="amber_400"),
                    ft.Row([day_dropdown, assign_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=10)
                ], spacing=10),
                padding=ft.Padding.only(top=10)
            )

            details_controls.append(assignment_section)

        else:
            edit_ing_field = ft.TextField(
                label="Ingredients (comma-separated)",
                value=", ".join(ingredients),
                multiline=True,
                min_lines=2,
                border_radius=8
            )
            edit_url_field = ft.TextField(
                label="Recipe Web URL",
                value=recipe_url,
                border_radius=8
            )

            def fetch_edit_ingredients(e):
                target_url = (edit_url_field.value or "").strip()
                if not target_url:
                    show_snackbar("⚠️ Enter a recipe URL first.", is_error=True)
                    return

                fetch_edit_btn.disabled = True
                show_snackbar("🌐 Fetching ingredients from URL...")
                page.update()

                def worker():
                    try:
                        extracted = fetch_ingredients_from_url(target_url)
                        if extracted:
                            edit_ing_field.value = ", ".join(extracted)
                            show_snackbar(f"✅ Imported {len(extracted)} ingredients!")
                        else:
                            show_snackbar("⚠️ No structured ingredients found at that URL.", is_error=True)
                    except Exception as ex:
                        show_snackbar(f"❌ Fetch failed: {ex}", is_error=True)
                    finally:
                        fetch_edit_btn.disabled = False
                        page.update()

                threading.Thread(target=worker, daemon=True).start()

            fetch_edit_btn = ft.TextButton(
                "🌐 Fetch Ingredients from URL",
                on_click=fetch_edit_ingredients
            )

            def save_recipe_edits(e):
                raw_ings = edit_ing_field.value or ""
                updated_ings = [i.strip().lower() for i in raw_ings.split(",") if i.strip()]
                if updated_ings:
                    recipe_ingredients[recipe_name] = updated_ings
                else:
                    recipe_ingredients[recipe_name] = list(DEFAULT_INGREDIENTS)
                save_json_file(INGREDIENTS_FILE, recipe_ingredients)

                updated_url = (edit_url_field.value or "").strip()
                if updated_url and updated_url != recipe_url:
                    if isinstance(meal_data.get(recipe_name), dict):
                        meal_data[recipe_name]["url"] = updated_url
                    else:
                        meal_data[recipe_name] = {"url": updated_url}
                    save_json_file(MEAL_DATA_FILE, meal_data)

                show_snackbar(f"✅ Updated '{recipe_name}'!")
                show_recipe_details(recipe_name, category, is_editing=False)

            save_btn = ft.Container(
                content=ft.Text("Save Changes", color="amber_300", weight=ft.FontWeight.BOLD),
                padding=12,
                bgcolor=ft.Colors.DEEP_PURPLE_700,
                border_radius=8,
                ink=True,
                on_click=save_recipe_edits
            )

            details_controls = [
                ft.Text(f"✏️ Editing: {recipe_name}", size=22, weight=ft.FontWeight.BOLD, color="amber_400"),
                ft.Text(f"Category: {category}", italic=True, color="grey_400"),
                ft.Divider(),
                edit_url_field,
                ft.Row([fetch_edit_btn], alignment=ft.MainAxisAlignment.END),
                edit_ing_field,
                ft.Row([save_btn], alignment=ft.MainAxisAlignment.END)
            ]

        details_card = ft.Card(
            content=ft.Container(
                content=ft.Column(details_controls, spacing=15),
                padding=20
            )
        )

        content_area.controls.extend([top_bar, ft.Divider(height=10), details_card])
        page.update()

    def show_weekly_plan(e=None):
        content_area.controls.clear()

        def toggle_lock_day(day_idx):
            is_currently_locked = current_week[day_idx].get("locked", False)
            current_week[day_idx]["locked"] = not is_currently_locked
            save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
            state_msg = "🔒 Locked" if not is_currently_locked else "🔓 Unlocked"
            show_snackbar(f"{state_msg} {DAYS[day_idx]}: {current_week[day_idx]['recipe']}")
            show_weekly_plan()
        
        def shuffle_week(e):
            nonlocal current_week
            if all(m.get("locked", False) for m in current_week if isinstance(m, dict)):
                show_snackbar("🔒 All days are locked! Unlock at least one day to reroll.", is_error=True)
                return

            selected_swap_idx[0] = None
            previous_week[:] = [dict(m) for m in current_week]
            current_week[:] = generate_balanced_week(recipes, existing_week=current_week)
            save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
            locked_count = sum(1 for m in current_week if m.get("locked", False))
            if locked_count > 0:
                show_snackbar(f"🎲 Rerolled unlocked days ({locked_count} locked meal(s) kept)!")
            else:
                show_snackbar("🎲 Rerolled entire week (unique meals, no back-to-back categories)!")
            show_weekly_plan()

        def undo_week_reroll(e):
            nonlocal current_week
            if previous_week:
                current_week[:] = [dict(m) for m in previous_week]
                previous_week.clear()
                save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
                show_snackbar("↩️ Restored previous weekly schedule!")
                show_weekly_plan()

        def reroll_single_day(day_idx):
            if current_week[day_idx].get("locked", False):
                show_snackbar(f"🔒 {DAYS[day_idx]} is locked! Unlock it first to reroll.", is_error=True)
                return

            selected_swap_idx[0] = None
            week_recipes = {m.get("recipe") for m in current_week if isinstance(m, dict)}

            adjacent_cats = set()
            if day_idx > 0:
                adjacent_cats.add(current_week[day_idx - 1].get("category"))
            if day_idx < len(current_week) - 1:
                adjacent_cats.add(current_week[day_idx + 1].get("category"))

            all_pairs = get_all_recipe_pairs(recipes)
            candidates = [
                p for p in all_pairs
                if p["recipe"] not in week_recipes and p["category"] not in adjacent_cats
            ]
            if not candidates:
                candidates = [p for p in all_pairs if p["recipe"] not in week_recipes]
            if not candidates:
                candidates = all_pairs

            new_meal = dict(random.choice(candidates))
            new_meal["locked"] = False
            current_week[day_idx] = new_meal
            save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
            show_snackbar(f"🎲 Rerolled {DAYS[day_idx]}: {new_meal['recipe']} ({new_meal['category']})")
            show_weekly_plan()

        def toggle_swap_day(day_idx):
            if selected_swap_idx[0] is None:
                selected_swap_idx[0] = day_idx
                show_weekly_plan()
            elif selected_swap_idx[0] == day_idx:
                selected_swap_idx[0] = None
                show_weekly_plan()
            else:
                first_idx = selected_swap_idx[0]
                current_week[first_idx], current_week[day_idx] = current_week[day_idx], current_week[first_idx]
                selected_swap_idx[0] = None
                save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
                show_snackbar(f"🔄 Swapped {DAYS[first_idx]} and {DAYS[day_idx]}!")
                show_weekly_plan()

        def open_manual_meal_picker(day_idx):
            selected_swap_idx[0] = None
            current_cat = current_week[day_idx].get("category", "")
            current_rec = current_week[day_idx].get("recipe", "")
            current_key = f"{current_cat}:::{current_rec}"

            all_items = []
            for cat in sorted(recipes.keys(), key=lambda x: x.lower()):
                for r in sorted(recipes[cat], key=lambda x: x.lower()):
                    all_items.append((cat, r))

            selected_key = [current_key]
            options_list = ft.ListView(height=260, spacing=6)

            def render_picker_options(filter_text=""):
                options_list.controls.clear()
                q = filter_text.strip().lower()
                for cat, r in all_items:
                    if not q or q in r.lower() or q in cat.lower():
                        item_key = f"{cat}:::{r}"
                        c_info = CATEGORY_CONFIG.get(cat, DEFAULT_CAT_CONFIG)
                        is_selected = (selected_key[0] == item_key)

                        def select_item(e, k=item_key):
                            selected_key[0] = k
                            render_picker_options(filter_field.value or "")
                            page.update()

                        tile = ft.Container(
                            content=ft.Row([
                                ft.Text(c_info["emoji"], size=18),
                                ft.Column([
                                    ft.Text(r, size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                                    ft.Text(cat, size=11, color="amber_300", italic=True)
                                ], expand=True, spacing=1),
                                ft.Icon(
                                    ft.Icons.CHECK_CIRCLE if is_selected else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                    color="amber_400" if is_selected else "grey_500",
                                    size=18
                                )
                            ]),
                            padding=10,
                            bgcolor=ft.Colors.DEEP_PURPLE_900 if is_selected else None,
                            border_radius=8,
                            ink=True,
                            on_click=select_item
                        )
                        options_list.controls.append(tile)

            filter_field = ft.TextField(
                label="Filter recipes...",
                prefix_icon=ft.Icons.SEARCH,
                border_radius=8,
                on_change=lambda e: (render_picker_options(e.control.value), page.update())
            )
            render_picker_options()

            def confirm_selection(e):
                if selected_key[0] and ":::" in selected_key[0]:
                    cat_name, rec_name = selected_key[0].split(":::", 1)
                    keep_lock = current_week[day_idx].get("locked", False)
                    current_week[day_idx] = {"category": cat_name, "recipe": rec_name, "locked": keep_lock}
                    save_json_file(WEEK_FILE, {"week_key": get_week_key(), "week": current_week})
                    close_dialog_safe(dlg)
                    show_snackbar(f"🍽️ Updated {DAYS[day_idx]} to '{rec_name}'!")
                    show_weekly_plan()

            dlg = ft.AlertDialog(
                title=ft.Text(f"Choose Meal for {DAYS[day_idx]}"),
                content=ft.Container(
                    content=ft.Column([
                        filter_field,
                        options_list
                    ], tight=True, spacing=10),
                    width=340
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: close_dialog_safe(dlg)),
                    ft.TextButton("Save Meal", on_click=confirm_selection)
                ]
            )
            open_dialog_safe(dlg)

        header_row = ft.Row([
            ft.Text("📅 Weekly Schedule", size=20, weight=ft.FontWeight.BOLD, color="amber_400"),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        reroll_week_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.SHUFFLE, color="amber_300", size=20),
                ft.Text("Reroll Entire Week", weight=ft.FontWeight.BOLD, color="amber_300", size=15)
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            bgcolor=ft.Colors.DEEP_PURPLE_700,
            padding=12,
            border_radius=8,
            ink=True,
            on_click=shuffle_week
        )
        
        content_area.controls.extend([header_row, reroll_week_btn])

        if previous_week:
            undo_week_banner = ft.Container(
                content=ft.Row([
                    ft.Text("🎲 Week rerolled", size=13, color="amber_200", expand=True),
                    ft.TextButton(
                        "Undo Reroll",
                        icon=ft.Icons.UNDO,
                        on_click=undo_week_reroll
                    )
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                bgcolor=ft.Colors.DEEP_PURPLE_900,
                border_radius=8
            )
            content_area.controls.append(undo_week_banner)

        if selected_swap_idx[0] is not None:
            swap_day_name = DAYS[selected_swap_idx[0]]
            swap_banner = ft.Container(
                content=ft.Row([
                    ft.Text(f"🔄 Swapping {swap_day_name} — tap swap on another day", size=13, color="amber_200", expand=True),
                    ft.TextButton("Cancel", on_click=lambda _: toggle_swap_day(selected_swap_idx[0]))
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                bgcolor=ft.Colors.DEEP_PURPLE_900,
                border_radius=8
            )
            content_area.controls.append(swap_banner)

        content_area.controls.append(ft.Divider(height=5))

        while len(current_week) < 7:
            cat = list(recipes.keys())[0] if recipes else "General"
            rec = recipes[cat][0] if cat in recipes and recipes[cat] else "Leftovers Night"
            current_week.append({"category": cat, "recipe": rec, "locked": False})

        today_meal = current_week[TODAY_IDX] if 0 <= TODAY_IDX < len(current_week) else current_week[0]
        today_locked = today_meal.get("locked", False)
        cat_info = CATEGORY_CONFIG.get(today_meal["category"], DEFAULT_CAT_CONFIG)
        today_url = get_recipe_url(today_meal["recipe"])

        spotlight = ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Column([
                            ft.Text(
                                f"✨ TODAY'S FEATURED MEAL ({DAYS[TODAY_IDX].upper()}) {'🔒' if today_locked else ''}".strip(),
                                weight=ft.FontWeight.BOLD,
                                color="amber_400"
                            ),
                            ft.Text(today_meal["recipe"], size=22, weight=ft.FontWeight.BOLD),
                            ft.Text(f"{cat_info['emoji']} Category: {today_meal['category']}", italic=True, color="grey_400")
                        ], expand=True),
                        ft.IconButton(
                            icon=ft.Icons.LOCK if today_locked else ft.Icons.LOCK_OPEN,
                            icon_color="amber_400" if today_locked else "grey_400",
                            tooltip="Unlock Today's Meal" if today_locked else "Lock Today's Meal (prevent reroll)",
                            on_click=lambda e: toggle_lock_day(TODAY_IDX)
                        ),
                        ft.IconButton(
                            icon=ft.Icons.EDIT_NOTE,
                            icon_color="amber_300",
                            tooltip="Pick Today's Meal",
                            on_click=lambda e: open_manual_meal_picker(TODAY_IDX)
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CASINO,
                            icon_color="grey_600" if today_locked else "amber_300",
                            tooltip="Locked (unlock to reroll)" if today_locked else "Reroll Today's Meal",
                            disabled=today_locked,
                            on_click=lambda e: reroll_single_day(TODAY_IDX)
                        ),
                        ft.IconButton(
                            icon=ft.Icons.LANGUAGE,
                            icon_color="amber_300",
                            tooltip=f"Open web recipe for {today_meal['recipe']}",
                            url=today_url
                        )
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0)
                ]),
                padding=15,
                bgcolor="deep_purple_900",
                border_radius=10,
                ink=True,
                on_click=lambda e, m=today_meal: show_recipe_details(m['recipe'], m['category'])
            )
        )
        content_area.controls.append(spotlight)
        content_area.controls.append(ft.Divider(height=10))

        for i in range(7):
            meal = current_week[i]
            is_today = (i == TODAY_IDX)
            is_locked = meal.get("locked", False)
            is_selected_swap = (selected_swap_idx[0] == i)
            c_info = CATEGORY_CONFIG.get(meal['category'], DEFAULT_CAT_CONFIG)
            meal_url = get_recipe_url(meal["recipe"])
            
            day_col = ft.Column([
                ft.Text(
                    f"{DAYS[i]} {'(Today)' if is_today else ''} • {meal['category']}".strip(),
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color="amber_300" if (is_today or is_selected_swap or is_locked) else "grey_400"
                ),
                ft.Text(f"{c_info['emoji']} {meal['recipe']}", size=14, weight=ft.FontWeight.W_500)
            ], expand=True, spacing=2)
            
            row_controls = [
                day_col,
                ft.IconButton(
                    icon=ft.Icons.LOCK if is_locked else ft.Icons.LOCK_OPEN,
                    icon_color="amber_400" if is_locked else "grey_400",
                    icon_size=18,
                    tooltip=f"Unlock {DAYS[i]}" if is_locked else f"Lock {DAYS[i]} (prevent reroll)",
                    on_click=lambda e, idx=i: toggle_lock_day(idx)
                ),
                ft.IconButton(
                    icon=ft.Icons.EDIT_NOTE,
                    icon_color="amber_300",
                    icon_size=18,
                    tooltip=f"Manually pick recipe for {DAYS[i]}",
                    on_click=lambda e, idx=i: open_manual_meal_picker(idx)
                ),
                ft.IconButton(
                    icon=ft.Icons.SWAP_VERT,
                    icon_color="amber_300" if is_selected_swap else "grey_400",
                    icon_size=18,
                    tooltip=f"Swap {DAYS[i]} with another day",
                    on_click=lambda e, idx=i: toggle_swap_day(idx)
                ),
                ft.IconButton(
                    icon=ft.Icons.CASINO,
                    icon_color="grey_600" if is_locked else "amber_400",
                    icon_size=18,
                    tooltip=f"{DAYS[i]} is locked" if is_locked else f"Reroll {DAYS[i]}",
                    disabled=is_locked,
                    on_click=lambda e, idx=i: reroll_single_day(idx)
                ),
                ft.IconButton(
                    icon=ft.Icons.LANGUAGE,
                    icon_color="amber_400",
                    icon_size=18,
                    tooltip="Open recipe web page",
                    url=meal_url
                )
            ]

            meal_container = ft.Container(
                content=ft.Row(row_controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=0),
                padding=ft.Padding.only(left=12, right=4, top=8, bottom=8),
                bgcolor=ft.Colors.DEEP_PURPLE_900 if is_selected_swap else None,
                ink=True,
                border_radius=8,
                on_click=lambda e, m=meal: show_recipe_details(m['recipe'], m['category'])
            )
            card = ft.Card(content=meal_container)
            content_area.controls.append(card)

        # --- BUTTON & ICON KEY CARD AT BOTTOM OF WEEKLY SCHEDULE ---
        def key_item(icon_name, label_text, icon_color="amber_300"):
            return ft.Row([
                ft.Icon(icon_name, color=icon_color, size=18),
                ft.Text(label_text, size=13, color="grey_300", expand=True)
            ], spacing=10)

        icon_key_card = ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("🔑 Button & Icon Key", size=15, weight=ft.FontWeight.BOLD, color="amber_400"),
                    ft.Divider(height=6),
                    key_item(ft.Icons.LOCK_OPEN, "Lock / Unlock meal (keeps it safe during week rerolls)", "amber_400"),
                    key_item(ft.Icons.EDIT_NOTE, "Manually pick a specific recipe for that day", "amber_300"),
                    key_item(ft.Icons.SWAP_VERT, "Swap meals between two days", "amber_300"),
                    key_item(ft.Icons.CASINO, "Reroll just that single day (when unlocked)", "amber_400"),
                    key_item(ft.Icons.LANGUAGE, "Open the recipe's webpage in your browser", "amber_400"),
                    key_item(ft.Icons.TOUCH_APP, "Tap any day card to view ingredients & recipe details", "grey_400"),
                ], spacing=8),
                padding=14,
                bgcolor=ft.Colors.DEEP_PURPLE_900,
                border_radius=10
            )
        )

        content_area.controls.extend([
            ft.Divider(height=10),
            icon_key_card
        ])
        
        page.update()

    def show_recipes(e=None):
        content_area.controls.clear()
        content_area.controls.append(ft.Text("📖 Recipe Collection", size=20, weight=ft.FontWeight.BOLD, color="amber_400"))
        
        def delete_recipe(category, recipe_name):
            if category in recipes and recipe_name in recipes[category]:
                recipes[category].remove(recipe_name)
                if not recipes[category]:
                    del recipes[category]
                
                save_json_file(RECIPE_FILE, recipes)
                
                if recipe_name in recipe_ingredients:
                    del recipe_ingredients[recipe_name]
                    save_json_file(INGREDIENTS_FILE, recipe_ingredients)
                
                show_snackbar(f"🗑️ Deleted '{recipe_name}'", is_error=True)
                update_recipe_list()

        results_list = ft.ListView(expand=1, spacing=8, padding=0)
        search_query = ""

        sort_dropdown = ft.Dropdown(
            label="Sort By",
            value="Category",
            options=[
                ft.dropdown.Option("Category"),
                ft.dropdown.Option("Alphabetical (A-Z)")
            ],
            width=180,
            border_radius=8
        )

        def update_recipe_list():
            results_list.controls.clear()
            query = search_query.strip().lower()
            sort_mode = sort_dropdown.value if sort_dropdown else "Category"
            
            match_found = False
            recipes_to_display = []

            if sort_mode == "Alphabetical (A-Z)":
                for cat, rec_list in recipes.items():
                    for r in rec_list:
                        if not query or query in r.lower() or query in cat.lower():
                            recipes_to_display.append((r, cat))
                recipes_to_display.sort(key=lambda x: x[0].lower())
            else:
                for cat, rec_list in recipes.items():
                    filtered_recs = [r for r in rec_list if not query or query in r.lower() or query in cat.lower()]
                    for r in sorted(filtered_recs, key=lambda x: x.lower()):
                        recipes_to_display.append((r, cat))

            if recipes_to_display:
                match_found = True
                for r, cat in recipes_to_display:
                    c_info = CATEGORY_CONFIG.get(cat, DEFAULT_CAT_CONFIG)
                    r_url = get_recipe_url(r)
                    
                    list_tile_content = ft.Container(
                        content=ft.Row([
                            ft.Text(c_info["emoji"], size=22),
                            ft.Column([
                                ft.Text(r, weight=ft.FontWeight.BOLD, size=14, color=ft.Colors.WHITE),
                                ft.Text(cat, size=11, color="amber_300", italic=True)
                            ], expand=True, spacing=2),
                            ft.IconButton(
                                icon=ft.Icons.LANGUAGE,
                                icon_color="amber_400",
                                icon_size=18,
                                tooltip=f"Open web recipe for {r}",
                                url=r_url
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_color="red_400",
                                icon_size=18,
                                tooltip=f"Delete {r}",
                                on_click=lambda e, c=cat, rec=r: delete_recipe(c, rec)
                            )
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=2),
                        padding=12,
                        bgcolor=ft.Colors.DEEP_PURPLE_900,
                        border_radius=8,
                        ink=True,
                        on_click=lambda e, recipe_name=r, category=cat: show_recipe_details(recipe_name, category)
                    )
                    
                    results_list.controls.append(ft.Card(content=list_tile_content))
            
            if not match_found:
                results_list.controls.append(ft.Text("No matching recipes found.", italic=True, color="grey_400"))
            
            page.update()

        sort_dropdown.on_change = lambda e: update_recipe_list()

        form_container = ft.Container(visible=False)

        def save_new_recipe(e):
            r_name = name_input.value.strip() if name_input.value else ""
            if not r_name:
                name_input.error_text = "Recipe name is required!"
                name_input.update()
                return

            r_cat = cat_dropdown.value if cat_dropdown.value else "Chicken"
            r_ing = [i.strip().lower() for i in ingredients_input.value.split(",") if i.strip()] if ingredients_input.value else ["salt", "pepper"]
            r_url = url_input.value.strip() if url_input.value else ""

            if r_cat not in recipes:
                recipes[r_cat] = []
            if r_name not in recipes[r_cat]:
                recipes[r_cat].append(r_name)
                save_json_file(RECIPE_FILE, recipes)

            if r_ing:
                recipe_ingredients[r_name] = r_ing
                save_json_file(INGREDIENTS_FILE, recipe_ingredients)
            if r_url:
                if isinstance(meal_data.get(r_name), dict):
                    meal_data[r_name]["url"] = r_url
                else:
                    meal_data[r_name] = {"url": r_url}
                save_json_file(MEAL_DATA_FILE, meal_data)

            name_input.value = ""
            ingredients_input.value = ""
            url_input.value = ""
            form_container.visible = False
            show_snackbar(f"✅ Successfully saved '{r_name}'!")
            update_recipe_list()

        def cancel_add_recipe(e):
            form_container.visible = False
            page.update()

        def open_add_form(e):
            form_container.visible = True
            page.update()

        name_input = ft.TextField(label="Recipe Name *", autofocus=True)
        cat_dropdown = ft.Dropdown(
            label="Category",
            options=[ft.dropdown.Option(c) for c in recipes.keys()],
            value=list(recipes.keys())[0] if recipes else "Chicken"
        )
        ingredients_input = ft.TextField(
            label="Ingredients (comma-separated)",
            hint_text="chicken, garlic, olive oil",
            multiline=True,
            min_lines=2
        )
        url_input = ft.TextField(
            label="Recipe Web URL (optional — auto-searches if blank)",
            hint_text="https://example.com/recipe"
        )

        def fetch_new_ingredients(e):
            target_url = (url_input.value or "").strip()
            if not target_url:
                show_snackbar("⚠️ Paste a recipe URL first.", is_error=True)
                return

            fetch_new_btn.disabled = True
            show_snackbar("🌐 Extracting ingredients...")
            page.update()

            def worker():
                try:
                    extracted = fetch_ingredients_from_url(target_url)
                    if extracted:
                        ingredients_input.value = ", ".join(extracted)
                        show_snackbar(f"✅ Populated {len(extracted)} ingredients!")
                    else:
                        show_snackbar("⚠️ Couldn't find structured ingredients on that page.", is_error=True)
                except Exception as ex:
                    show_snackbar(f"❌ Fetch failed: {ex}", is_error=True)
                finally:
                    fetch_new_btn.disabled = False
                    page.update()

            threading.Thread(target=worker, daemon=True).start()

        fetch_new_btn = ft.TextButton(
            "🌐 Auto-Fill Ingredients from URL",
            on_click=fetch_new_ingredients
        )

        cancel_btn = ft.Container(
            content=ft.Text("Cancel", color="grey_300", weight=ft.FontWeight.BOLD),
            padding=10, ink=True, border_radius=5, on_click=cancel_add_recipe
        )
        save_btn = ft.Container(
            content=ft.Text("Save Recipe", color="amber_300", weight=ft.FontWeight.BOLD),
            padding=10, ink=True, border_radius=5, bgcolor=ft.Colors.DEEP_PURPLE_700, on_click=save_new_recipe
        )

        form_container.content = ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("✨ Add New Recipe", size=18, weight=ft.FontWeight.BOLD, color="amber_400"),
                    name_input,
                    cat_dropdown,
                    url_input,
                    ft.Row([fetch_new_btn], alignment=ft.MainAxisAlignment.END),
                    ingredients_input,
                    ft.Row([cancel_btn, save_btn], alignment=ft.MainAxisAlignment.END, spacing=10)
                ], spacing=12),
                padding=15
            )
        )

        add_recipe_container = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.ADD, color="white"),
                ft.Text("Add New Recipe", weight=ft.FontWeight.BOLD, color="white")
            ], alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=ft.Colors.DEEP_PURPLE_700,
            padding=12,
            border_radius=8,
            ink=True,
            on_click=open_add_form
        )

        def refresh_all_recipe_ingredients(e):
            targets = []
            for cat, r_list in recipes.items():
                for r in r_list:
                    url = get_recipe_url(r)
                    if url and url.startswith(("http://", "https://")) and "allrecipes.com/search" not in url:
                        targets.append((r, url))

            if not targets:
                show_snackbar("⚠️ No valid recipe URLs found to refresh.", is_error=True)
                return

            refresh_all_btn.disabled = True
            show_snackbar(f"🌐 Updating ingredients for {len(targets)} recipes...")
            page.update()

            def worker():
                updated_count = 0
                failed_count = 0
                for idx, (r_name, r_url) in enumerate(targets, start=1):
                    try:
                        extracted = fetch_ingredients_from_url(r_url)
                        if extracted:
                            recipe_ingredients[r_name] = extracted
                            updated_count += 1
                        else:
                            failed_count += 1
                    except Exception:
                        failed_count += 1
                    time.sleep(0.3)

                save_json_file(INGREDIENTS_FILE, recipe_ingredients)
                show_snackbar(f"✅ Refreshed {updated_count} recipes ({failed_count} skipped/failed).")
                refresh_all_btn.disabled = False
                update_recipe_list()

            threading.Thread(target=worker, daemon=True).start()

        refresh_all_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.SYNC, color="white", size=18),
                ft.Text("Refresh All Ingredients from URLs", weight=ft.FontWeight.BOLD, color="white")
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            bgcolor=ft.Colors.DEEP_PURPLE_800,
            padding=10,
            border_radius=8,
            ink=True,
            on_click=refresh_all_recipe_ingredients
        )

        def on_search_change(e):
            nonlocal search_query
            search_query = e.control.value
            update_recipe_list()

        search_input = ft.TextField(
            label="Search recipes...",
            prefix_icon=ft.Icons.SEARCH,
            border_radius=8,
            expand=True,
            on_change=on_search_change
        )

        search_and_sort_row = ft.Row([
            search_input,
            sort_dropdown
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=10)

        content_area.controls.extend([
            add_recipe_container,
            refresh_all_btn,
            form_container,
            ft.Divider(height=10),
            search_and_sort_row,
            ft.Divider(height=10),
            results_list
        ])
        
        update_recipe_list()
        page.update()

    def show_pantry(e=None):
        content_area.controls.clear()
        content_area.controls.append(ft.Text("📦 Pantry Inventory", size=20, weight=ft.FontWeight.BOLD, color="amber_400"))
        
        pantry_list_col = ft.Column()

        def delete_pantry_item(item_to_remove):
            if item_to_remove in pantry:
                pantry.remove(item_to_remove)
                save_json_file(PANTRY_FILE, pantry)
                show_snackbar(f"Removed '{item_to_remove}' from pantry", is_error=True)
                refresh_pantry_list()

        def refresh_pantry_list():
            pantry_list_col.controls.clear()
            if not pantry:
                pantry_list_col.controls.append(ft.Text("Your pantry is empty.", italic=True, color="grey_400"))
            else:
                for item in sorted(pantry):
                    item_row = ft.Row([
                        ft.Text(f"• {str(item).capitalize()}", expand=True),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_color="red_400",
                            tooltip=f"Remove {item}",
                            on_click=lambda e, it=item: delete_pantry_item(it)
                        )
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                    pantry_list_col.controls.append(item_row)
            page.update()

        new_item_input = ft.TextField(label="Add pantry item", expand=True)

        def add_item_clicked(e):
            val = new_item_input.value.strip().lower() if new_item_input.value else ""
            if val and val not in [str(p).lower() for p in pantry]:
                pantry.append(val)
                save_json_file(PANTRY_FILE, pantry)
                new_item_input.value = ""
                show_snackbar(f"Added '{val}' to pantry!")
                refresh_pantry_list()

        add_btn = ft.TextButton("Add Item", on_click=add_item_clicked)
        add_row = ft.Row([new_item_input, add_btn])

        content_area.controls.append(add_row)
        content_area.controls.append(ft.Divider())
        content_area.controls.append(pantry_list_col)
        
        refresh_pantry_list()
        page.update()

    def show_grocery_list(e=None):
        content_area.controls.clear()

        def toggle_plain_text_view(e):
            plain_text_mode[0] = not plain_text_mode[0]
            show_grocery_list()

        content_area.controls.append(
            ft.Text("🛒 Automated Grocery List (Web)", size=20, weight=ft.FontWeight.BOLD, color="amber_400")
        )

        extra_item_input = ft.TextField(
            label="Add extra item (e.g., paper towels, coffee)...",
            expand=True,
            border_radius=8
        )

        def add_extra_grocery_item(e=None):
            raw_val = (extra_item_input.value or "").strip()
            if not raw_val:
                return
            low_val = raw_val.lower()
            existing_extra_lower = {str(x).strip().lower() for x in extra_grocery_items}
            if low_val not in existing_extra_lower:
                extra_grocery_items.append(low_val)
                save_json_file(EXTRA_GROCERY_FILE, extra_grocery_items)
                show_debug_banner(f"Added '{raw_val.capitalize()}' to shopping list!", is_error=False, seconds=3)
            else:
                show_debug_banner(f"'{raw_val.capitalize()}' is already on the list!", is_error=True, seconds=3)
            extra_item_input.value = ""
            show_grocery_list()

        extra_item_input.on_submit = add_extra_grocery_item

        add_extra_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.ADD, color="amber_300", size=18),
                ft.Text("Add", color="amber_300", weight=ft.FontWeight.BOLD)
            ], spacing=4),
            bgcolor=ft.Colors.DEEP_PURPLE_700,
            padding=12,
            border_radius=8,
            ink=True,
            on_click=add_extra_grocery_item
        )

        content_area.controls.append(ft.Row([extra_item_input, add_extra_btn], spacing=8))

        view_toggle_btn = ft.Container(
            content=ft.Row([
                ft.Icon(
                    ft.Icons.CHECKLIST if plain_text_mode[0] else ft.Icons.NOTES,
                    color="amber_300",
                    size=20
                ),
                ft.Text(
                    "Switch to Interactive Checklist" if plain_text_mode[0] else "Switch to Plain-Text View (Mobile Copy)",
                    weight=ft.FontWeight.BOLD,
                    color="amber_300",
                    size=14
                )
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            bgcolor=ft.Colors.DEEP_PURPLE_700,
            padding=12,
            border_radius=8,
            ink=True,
            on_click=toggle_plain_text_view
        )
        content_area.controls.append(view_toggle_btn)

        pantry_normalized = {normalize_ingredient_key(p) for p in pantry if p}
        pantry_raw_lower = {str(p).strip().lower() for p in pantry if p}

        merged_entries: dict[str, dict] = {}
        for meal in current_week:
            if isinstance(meal, dict):
                recipe_name = meal.get("recipe", "")
                ingredients = recipe_ingredients.get(recipe_name, DEFAULT_INGREDIENTS)
                seen_in_this_recipe = set()
                for ing in ingredients:
                    if not ing:
                        continue
                    norm_key = normalize_ingredient_key(ing)
                    if not norm_key or norm_key in seen_in_this_recipe:
                        continue
                    seen_in_this_recipe.add(norm_key)

                    if norm_key in pantry_normalized or norm_key in pantry_raw_lower:
                        continue

                    if norm_key not in merged_entries:
                        merged_entries[norm_key] = {
                            "name": norm_key,
                            "count": 1,
                            "is_extra": False
                        }
                    else:
                        merged_entries[norm_key]["count"] += 1

        for extra_it in extra_grocery_items:
            clean_extra = str(extra_it).strip().lower()
            if not clean_extra:
                continue
            norm_extra = normalize_ingredient_key(clean_extra)
            if norm_extra in merged_entries:
                merged_entries[norm_extra]["is_extra"] = True
            else:
                merged_entries[clean_extra] = {
                    "name": clean_extra,
                    "count": 1,
                    "is_extra": True
                }

        all_to_buy_entries = list(merged_entries.values())
        grouped_to_buy = group_grocery_entries_by_aisle(all_to_buy_entries)

        def build_plain_text_grocery_list() -> str:
            if not grouped_to_buy:
                return "Grocery List\n\nEverything needed is already in your pantry!"
            lines = ["Grocery List"]
            for aisle, aisle_entries in grouped_to_buy.items():
                lines.append(f"\n[{aisle}]")
                for entry in aisle_entries:
                    lines.append(f"- {format_grocery_entry_label(entry)}")
            return "\n".join(lines)

        export_link_text = ft.Text("", color="amber_300", weight=ft.FontWeight.BOLD, size=14)
        export_link_banner = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.OPEN_IN_NEW, color="amber_300", size=18),
                export_link_text
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            bgcolor=ft.Colors.DEEP_PURPLE_900,
            padding=12,
            border_radius=8,
            ink=True,
            visible=False
        )

        def show_export_link(filename: str, label: str):
            export_link_text.value = label
            export_link_banner.url = f"/{filename}"
            export_link_banner.visible = True
        
        def export_web_pdf(e):
            if not FPDF_AVAILABLE:
                show_debug_banner("fpdf2 is not installed on the server (pip install fpdf2).", is_error=True, seconds=4)
                return

            try:
                from fpdf import FPDF
                from fpdf.enums import XPos, YPos

                cleanup_old_exports()

                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 16)
                pdf.cell(0, 10, "Weekly Menu & Grocery List", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font("Helvetica", "", 11)
                pdf.cell(0, 8, f"Generated: {datetime.today().strftime('%Y-%m-%d')}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(4)

                pdf.set_font("Helvetica", "B", 13)
                pdf.cell(0, 8, "This Week's Menu:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font("Helvetica", "", 11)
                for idx, day_name in enumerate(DAYS):
                    meal = current_week[idx] if idx < len(current_week) and isinstance(current_week[idx], dict) else {}
                    rec = meal.get("recipe", "Unassigned")
                    pdf.cell(0, 7, f"{day_name}: {rec}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

                pdf.ln(5)
                pdf.set_font("Helvetica", "B", 13)
                pdf.cell(0, 8, "Shopping List (By Aisle):", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

                if not grouped_to_buy:
                    pdf.set_font("Helvetica", "", 11)
                    pdf.cell(0, 8, "Everything needed is already in your pantry.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                else:
                    for aisle, aisle_entries in grouped_to_buy.items():
                        pdf.ln(2)
                        pdf.set_font("Helvetica", "B", 11)
                        pdf.cell(0, 7, aisle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                        pdf.set_font("Helvetica", "", 11)
                        for entry in aisle_entries:
                            pdf.cell(0, 6, f"  [ ] {format_grocery_entry_label(entry)}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"grocery_list_{timestamp}.pdf"
                pdf_filepath = os.path.join(ASSETS_DIR, filename)
                pdf.output(pdf_filepath)

                show_export_link(filename, f"Tap here if PDF didn't open ({filename})")
                open_url_in_browser(page, f"/{filename}")
                show_debug_banner(f"Generated {filename}!", is_error=False, seconds=3)
            except Exception as ex:
                show_debug_banner(f"Web PDF Error: {str(ex)}", is_error=True, seconds=4)

        def print_web_grocery_list(e):
            cleanup_old_exports()
            date_str = datetime.today().strftime("%B %d, %Y")

            menu_rows = []
            for idx, day_name in enumerate(DAYS):
                meal = current_week[idx] if idx < len(current_week) and isinstance(current_week[idx], dict) else {}
                rec = meal.get("recipe", "Unassigned")
                cat = meal.get("category", "")
                cat_badge = f" <span class='cat'>({html.escape(cat)})</span>" if cat else ""
                menu_rows.append(
                    f"<div class='menu-item'><strong>{html.escape(day_name[:3])}:</strong> "
                    f"{html.escape(rec)}{cat_badge}</div>"
                )
            menu_html = f"<div class='menu-grid'>{''.join(menu_rows)}</div>"

            if not grouped_to_buy:
                items_html = "<p style='font-style: italic; color: #555;'>Everything needed is already in your pantry!</p>"
            else:
                aisle_sections = []
                for aisle, aisle_entries in grouped_to_buy.items():
                    li_tags = "".join(
                        f"<li><span class='box'>☐</span> {html.escape(format_grocery_entry_label(entry))}</li>"
                        for entry in aisle_entries
                    )
                    aisle_sections.append(
                        f"<div class='aisle-block'><h3>{html.escape(aisle)}</h3><ul>{li_tags}</ul></div>"
                    )
                items_html = f"<div class='aisles-wrap'>{''.join(aisle_sections)}</div>"

            html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Weekly Menu & Grocery List - {date_str}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    max-width: 650px;
    margin: 24px auto;
    padding: 0 20px;
    color: #111;
  }}
  h1 {{ font-size: 22px; margin-bottom: 4px; border-bottom: 2px solid #333; padding-bottom: 6px; }}
  h2 {{ font-size: 16px; margin: 18px 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px; color: #333; }}
  h3 {{ font-size: 14px; margin: 10px 0 4px 0; color: #222; border-bottom: 1px solid #bbb; padding-bottom: 2px; }}
  .meta {{ color: #555; font-size: 12px; margin-bottom: 14px; }}
  .menu-grid {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 6px 20px;
    background: #f7f7f9; border: 1px solid #ddd; border-radius: 6px;
    padding: 10px 14px; margin-bottom: 18px;
  }}
  .menu-item {{ font-size: 13px; line-height: 1.4; }}
  .cat {{ color: #666; font-size: 11px; }}
  .aisles-wrap {{ columns: 2; column-gap: 30px; }}
  .aisle-block {{ break-inside: avoid; margin-bottom: 12px; }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  li {{ font-size: 14px; padding: 4px 0; border-bottom: 1px dashed #ddd; }}
  .box {{ font-size: 16px; margin-right: 8px; }}
  @media print {{ body {{ margin: 10px; }} .menu-grid {{ background: #fff; }} }}
</style>
</head>
<body onload="window.print()">
  <h1>🍽 Weekly Menu & Grocery List</h1>
  <div class="meta">Generated on {date_str} • {len(all_to_buy_entries)} item(s) to buy</div>
  <h2>This Week's Dinner Schedule</h2>
  {menu_html}
  <h2>Shopping List (By Aisle)</h2>
  {items_html}
</body>
</html>"""

            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                print_filename = f"print_menu_{timestamp}.html"
                print_filepath = os.path.join(ASSETS_DIR, print_filename)
                with open(print_filepath, "w", encoding="utf-8") as f:
                    f.write(html_doc)
                
                show_export_link(print_filename, "Tap here if Print View didn't open")
                open_url_in_browser(page, f"/{print_filename}")
                show_debug_banner("Generated print view!", is_error=False, seconds=3)
            except Exception as ex:
                show_debug_banner(f"Print error: {str(ex)}", is_error=True, seconds=4)

        def restore_item_to_list(checked_record: dict):
            item_name = checked_record["name"]
            is_extra = checked_record.get("is_extra", False)

            if is_extra:
                if item_name not in [str(x).strip().lower() for x in extra_grocery_items]:
                    extra_grocery_items.append(item_name)
                    save_json_file(EXTRA_GROCERY_FILE, extra_grocery_items)
            else:
                norm_target = normalize_ingredient_key(item_name)
                for p_item in list(pantry):
                    low_p = str(p_item).strip().lower()
                    if low_p == item_name.strip().lower() or normalize_ingredient_key(low_p) == norm_target:
                        pantry.remove(p_item)
                save_json_file(PANTRY_FILE, pantry)

            for rec in list(recently_checked_off):
                if rec["name"] == item_name:
                    recently_checked_off.remove(rec)

            show_debug_banner(f"Returned '{item_name.capitalize()}' to list", is_error=False, seconds=3)
            show_grocery_list()

        def undo_last_checked(e):
            if recently_checked_off:
                last_record = recently_checked_off.pop()
                restore_item_to_list(last_record)

        def on_reactive_checkbox_change(entry: dict, e):
            if e.control.value:
                item_name = entry["name"]
                is_extra = entry.get("is_extra", False)

                if is_extra:
                    for x in list(extra_grocery_items):
                        if str(x).strip().lower() == item_name.strip().lower():
                            extra_grocery_items.remove(x)
                    save_json_file(EXTRA_GROCERY_FILE, extra_grocery_items)
                    msg = f"Checked off '{item_name.capitalize()}'"
                else:
                    if item_name not in [str(p).strip().lower() for p in pantry]:
                        pantry.append(item_name)
                        save_json_file(PANTRY_FILE, pantry)
                    msg = f"Added '{item_name.capitalize()}' to pantry"

                if not any(r["name"] == item_name for r in recently_checked_off):
                    recently_checked_off.append({"name": item_name, "is_extra": is_extra})

                show_debug_banner(msg, is_error=False, seconds=3)
                show_grocery_list()

        if plain_text_mode[0]:
            plain_str = build_plain_text_grocery_list()
            content_area.controls.append(
                ft.Text(
                    "Select or long-press the text below to copy to your phone or browser:",
                    color="amber_200",
                    size=13
                )
            )
            plain_card = ft.Card(
                content=ft.Container(
                    content=ft.Text(plain_str, selectable=True, size=15),
                    padding=16
                )
            )
            content_area.controls.append(plain_card)
        else:
            if recently_checked_off:
                last_added = recently_checked_off[-1]["name"].capitalize()
                undo_banner = ft.Container(
                    content=ft.Row([
                        ft.Text(f"Last checked: {last_added}", size=13, color="amber_200", expand=True),
                        ft.TextButton("Undo", icon=ft.Icons.UNDO, on_click=undo_last_checked)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                    bgcolor=ft.Colors.DEEP_PURPLE_900,
                    border_radius=8
                )
                content_area.controls.append(undo_banner)

            if not grouped_to_buy:
                content_area.controls.append(ft.Text("🎉 You have everything in your pantry for this week's meals!", italic=True, color="green_400"))
            else:
                content_area.controls.append(ft.Text("Tap items as you shop to sync them immediately:", color="grey_400"))
                shopping_col = ft.Column(spacing=10)
                for aisle, aisle_entries in grouped_to_buy.items():
                    aisle_card_items = [
                        ft.Text(f"📍 {aisle}", weight=ft.FontWeight.BOLD, size=15, color="amber_300")
                    ]
                    for entry in aisle_entries:
                        cb = ft.Checkbox(
                            label=format_grocery_entry_label(entry),
                            value=False,
                            on_change=lambda e, ent=entry: on_reactive_checkbox_change(ent, e)
                        )
                        aisle_card_items.append(cb)
                    shopping_col.controls.append(
                        ft.Card(
                            content=ft.Container(
                                content=ft.Column(aisle_card_items, spacing=2),
                                padding=12
                            )
                        )
                    )
                content_area.controls.append(shopping_col)

            if recently_checked_off:
                content_area.controls.append(ft.Divider(height=15))
                content_area.controls.append(ft.Text("✅ Recently Checked Off (Tap to Undo):", size=14, weight=ft.FontWeight.BOLD, color="grey_400"))
                
                history_col = ft.Column(spacing=4)
                for checked_rec in reversed(recently_checked_off):
                    checked_name = checked_rec["name"]
                    history_row = ft.Row([
                        ft.Text(
                            f"• {checked_name.capitalize()}",
                            color="grey_500",
                            style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH),
                            expand=True
                        ),
                        ft.IconButton(
                            icon=ft.Icons.UNDO,
                            icon_color="amber_400",
                            icon_size=18,
                            tooltip=f"Put {checked_name} back on list",
                            on_click=lambda e, rec=checked_rec: restore_item_to_list(rec)
                        )
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                    history_col.controls.append(history_row)
                
                content_area.controls.append(history_col)

        pdf_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.PICTURE_AS_PDF, color="white", size=18),
                ft.Text("Open / Download PDF in Browser", weight=ft.FontWeight.BOLD, color="white")
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            bgcolor=ft.Colors.DEEP_PURPLE_800,
            padding=12,
            border_radius=8,
            ink=True,
            on_click=export_web_pdf
        )

        print_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.PRINT, color="white", size=18),
                ft.Text("Print Menu & Grocery List (Web)", weight=ft.FontWeight.BOLD, color="white")
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            bgcolor=ft.Colors.DEEP_PURPLE_800,
            padding=12,
            border_radius=8,
            ink=True,
            on_click=print_web_grocery_list
        )
        
        content_area.controls.extend([
            ft.Divider(height=15),
            pdf_btn,
            print_btn,
            export_link_banner
        ])
            
        page.update()

    def show_timers(e=None):
        content_area.controls.clear()
        content_area.controls.append(ft.Text("⏱️ Kitchen Multi-Timer", size=20, weight=ft.FontWeight.BOLD, color="amber_400"))
        
        timers_col = ft.Column(spacing=15)

        def timer_ticker():
            while True:
                time.sleep(1)
                active_changed = False
                for t in timers_list:
                    if t.get("running", False) and t.get("remaining", 0) > 0:
                        t["remaining"] -= 1
                        active_changed = True
                        if t["remaining"] == 0:
                            t["running"] = False
                            show_snackbar(f"⏰ ALARM: '{t['title']}' timer finished!", is_error=True)
                if active_changed:
                    try:
                        refresh_timers_display()
                    except:
                        pass

        if not getattr(page, "_timer_thread_started", False):
            page._timer_thread_started = True
            threading.Thread(target=timer_ticker, daemon=True).start()

        def delete_timer(timer_dict):
            if timer_dict in timers_list:
                timer_dict["running"] = False
                timers_list.remove(timer_dict)
                save_json_file(TIMERS_FILE, timers_list)
                show_snackbar(f"🗑️ Deleted timer '{timer_dict['title']}'", is_error=True)
                refresh_timers_display()

        def refresh_timers_display():
            timers_col.controls.clear()
            if not timers_list:
                timers_col.controls.append(ft.Text("No active timers. Add one below!", italic=True, color="grey_400"))
            else:
                for t in timers_list:
                    rem = t.get("remaining", 0)
                    mins = rem // 60
                    secs = rem % 60
                    time_str = f"{mins:02d}:{secs:02d}"
                    
                    is_running = t.get("running", False)
                    timer_color = "green_400" if is_running else ("amber_400" if rem > 0 else "red_400")
                    timer_text = ft.Text(f"{time_str} remaining", size=18, weight=ft.FontWeight.BOLD, color=timer_color)

                    def toggle_timer(e, t_dict=t):
                        if t_dict["remaining"] <= 0:
                            t_dict["remaining"] = t_dict["total_seconds"]
                        t_dict["running"] = not t_dict.get("running", False)
                        refresh_timers_display()

                    def reset_timer(e, t_dict=t):
                        t_dict["running"] = False
                        t_dict["remaining"] = t_dict["total_seconds"]
                        refresh_timers_display()

                    t_btn = ft.TextButton(
                        "Pause" if is_running else "Start",
                        icon=ft.Icons.PAUSE if is_running else ft.Icons.PLAY_ARROW,
                        on_click=toggle_timer
                    )
                    reset_btn = ft.IconButton(
                        icon=ft.Icons.REFRESH,
                        icon_color="grey_400",
                        tooltip="Reset Timer",
                        on_click=reset_timer
                    )
                    del_btn = ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color="red_400",
                        tooltip=f"Delete {t['title']}",
                        on_click=lambda e, t_dict=t: delete_timer(t_dict)
                    )

                    card_row = ft.Row([
                        ft.Column([
                            ft.Text(t['title'], weight=ft.FontWeight.BOLD, size=15),
                            timer_text,
                            ft.Row([t_btn, reset_btn], spacing=5)
                        ], expand=True, spacing=4),
                        del_btn
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

                    card = ft.Card(content=ft.Container(content=card_row, padding=15))
                    timers_col.controls.append(card)
            
            try:
                page.update()
            except:
                pass

        timer_name_input = ft.TextField(label="Timer Name", hint_text="Boil Pasta", expand=True)
        timer_mins_input = ft.TextField(label="Minutes", hint_text="10", width=90)

        def add_custom_timer(e):
            name = timer_name_input.value.strip() if timer_name_input.value else "Custom Timer"
            try:
                mins = int(timer_mins_input.value.strip())
            except:
                mins = 5
            
            total_sec = mins * 60
            new_timer = {"title": name, "total_seconds": total_sec, "remaining": total_sec, "running": False}
            timers_list.append(new_timer)
            save_json_file(TIMERS_FILE, timers_list)

            timer_name_input.value = ""
            timer_mins_input.value = ""
            refresh_timers_display()
            show_snackbar(f"⏱️ Added timer: {name} ({mins} mins)")

        add_timer_btn = ft.TextButton("Add Timer", on_click=add_custom_timer)
        add_timer_row = ft.Row([timer_name_input, timer_mins_input, add_timer_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        content_area.controls.extend([
            add_timer_row,
            ft.Divider(height=20),
            timers_col
        ])
        refresh_timers_display()
        page.update()

    def nav_changed(e):
        index = getattr(e.control, "selected_index", 0) if e and e.control else 0
        if index == 0:
            show_weekly_plan()
        elif index == 1:
            show_recipes()
        elif index == 2:
            show_pantry()
        elif index == 3:
            show_grocery_list()
        elif index == 4:
            show_timers()

    page.navigation_bar = ft.NavigationBar(
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.DATE_RANGE, label="Schedule"),
            ft.NavigationBarDestination(icon=ft.Icons.BOOK, label="Recipes"),
            ft.NavigationBarDestination(icon=ft.Icons.STOREFRONT, label="Pantry"),
            ft.NavigationBarDestination(icon=ft.Icons.SHOPPING_CART, label="Shopping"),
            ft.NavigationBarDestination(icon=ft.Icons.TIMER, label="Timers"),
        ],
        on_change=nav_changed
    )

    show_weekly_plan()
    page.add(debug_banner, content_area)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    if hasattr(ft, "run"):
        ft.run(main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=port, assets_dir=ASSETS_DIR)
    else:
        ft.app(target=main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=port, assets_dir=ASSETS_DIR)