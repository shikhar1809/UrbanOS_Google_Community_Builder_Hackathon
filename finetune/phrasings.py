"""Natural phrasings per issue type, for augmenting the seeded reports.

Why this file has to exist
--------------------------
The 1007 seeded reports are rendered from five templates with the tag text
substituted in literally:

    "Please look into the streetlights not working situation in Alambagh."
    "There is a severe issue of transformer spark in Mahanagar."

Every report of an issue therefore CONTAINS its own label. A model trained on
that learns a string match -- "the words 'streetlights not working' appear, so
emit street_lighting and LESA" -- and learns nothing that transfers to how a
citizen actually writes ("MG Road has been pitch dark since Diwali"). The eval
cases are all phrased differently from the templates, which is precisely why
the base model fails them and why training on the raw export would not help.

So the seeded rows are re-rendered through the phrasings below. The LABEL still
comes from the stored record; only the wording is replaced, with text that
describes the issue the way a person would. Most phrasings deliberately avoid
the canonical tag's own words, so the mapping has to be learned semantically.

Language coverage is weighted to what the system actually receives: romanised
Hinglish is the most common real input, and Devanagari Hindi is second. Urdu
and Bengali appear because Lucknow's intake does receive them and because they
are the easiest regression to miss.

A rule about the eval set
-------------------------
Nothing here is copied from `evals/dataset.py`. Training on the eval text would
make every score meaningless, so these were written separately, and
`tests/test_finetune_dataset.py` asserts that no eval description appears in the
built training set. If a phrasing ever collides with an eval case, the test
fails and the phrasing is the thing that changes.

Format: PHRASINGS[canonical_tag] = [(text, language), ...] where {area} is the
report's area name.
"""

from __future__ import annotations

PHRASINGS: dict[str, list[tuple[str, str]]] = {
    # --- Electricity. The routing failure the eval measures lives here. ------
    "street_lighting": [
        ("The lane outside our house in {area} stays pitch dark after sunset", "English"),
        ("No light at all on our road in {area} for the past three weeks", "English"),
        ("The lamp posts in {area} have stopped glowing, it is unsafe for women returning late", "English"),
        ("Our colony road in {area} has had no illumination since the last storm", "English"),
        ("Please get the poles on our stretch in {area} to light up again", "English"),
        ("{area} me raat ko bilkul andhera rehta hai, koi light nahi jalti", "English"),
        ("Humari gali {area} me shaam ke baad ekdum kala andhera ho jata hai", "English"),
        ("{area} ki sadak par lagi batti kai dino se band padi hai", "English"),
        ("{area} में हमारी सड़क पर रात में कोई रोशनी नहीं रहती", "Hindi"),
        ("{area} के खंभों की लाइटें एक महीने से बंद पड़ी हैं", "Hindi"),
        ("{area} میں ہماری گلی رات کو بالکل اندھیری رہتی ہے", "Urdu"),
        ("{area} এলাকায় সন্ধ্যার পর রাস্তায় কোনো আলো থাকে না", "Bengali"),
    ],
    "electrical_hazard": [
        ("A transformer near our market in {area} keeps throwing sparks", "English"),
        ("Cables are hanging low over the footpath in {area} and people brush against them", "English"),
        ("There is crackling and smoke from the electric pole outside our gate in {area}", "English"),
        ("An exposed live cable is lying along the boundary wall in {area}", "English"),
        ("The junction box in {area} is open and wires are visible to children", "English"),
        ("{area} me transformer se chingari nikal rahi hai, bahut darr lagta hai", "English"),
        ("{area} me taar neeche jhool rahe hain, kabhi bhi haadsa ho sakta hai", "English"),
        ("{area} में बिजली के खंभे से चिंगारी निकल रही है", "Hindi"),
        ("{area} में खुले तार लटक रहे हैं, बच्चों को खतरा है", "Hindi"),
        ("{area} میں بجلی کے تار نیچے لٹک رہے ہیں", "Urdu"),
    ],
    "power_supply": [
        ("We lose electricity six or seven times a day in {area}", "English"),
        ("Supply in {area} goes off for hours every afternoon without warning", "English"),
        ("Voltage in {area} drops so low that nothing runs properly", "English"),
        ("Our area {area} has unscheduled cuts all through the night", "English"),
        ("{area} me din bhar bijli aati jaati rehti hai", "English"),
        ("{area} में बिजली दिन में कई बार चली जाती है", "Hindi"),
    ],

    # --- Water, sewage and drainage -> Jal Sansthan --------------------------
    "water_supply": [
        ("Our taps in {area} have been completely dry for four days", "English"),
        ("No supply has reached our mohalla in {area} this whole week", "English"),
        ("We are buying tankers because nothing comes through the connection in {area}", "English"),
        ("Pressure in {area} is so weak that nothing reaches the upper floor", "English"),
        ("{area} me paani bilkul nahi aa raha, char din ho gaye", "English"),
        ("{area} ke nal se ek bund paani nahi aata subah se", "English"),
        ("{area} में पिछले कई दिनों से नल सूखे पड़े हैं", "Hindi"),
        ("{area} میں کئی دنوں سے پانی نہیں آ رہا", "Urdu"),
        ("{area} এলাকায় কয়েকদিন ধরে জল আসছে না", "Bengali"),
    ],
    "water_quality": [
        ("The water coming to our homes in {area} is muddy and smells foul", "English"),
        ("Supply in {area} is yellowish and children have fallen ill", "English"),
        ("What comes out of the tap in {area} is not fit to drink", "English"),
        ("{area} me nal ka paani gandha aa raha hai, badbu bhi hai", "English"),
        ("{area} में नल से गंदा पानी आ रहा है", "Hindi"),
    ],
    "water_pipeline_repair": [
        ("A supply line has burst outside our house in {area} and water is running to waste", "English"),
        ("There is a leak in the main line in {area} that nobody has attended to", "English"),
        ("The connection pipe in {area} has cracked and the road is flooded", "English"),
        ("{area} me paani ki pipe phat gayi hai, saara paani barbaad ho raha hai", "English"),
        ("{area} में पाइपलाइन टूट गई है और पानी बह रहा है", "Hindi"),
    ],
    "sewage_overflow": [
        ("Drain water is backing up and spreading outside our gate in {area}", "English"),
        ("The sewer line in {area} is overflowing onto the lane and the smell is unbearable", "English"),
        ("Dirty water from the drain has entered our compound in {area}", "English"),
        ("{area} me naali ka gandha paani sadak par bah raha hai", "English"),
        ("{area} में सीवर का पानी गली में भर गया है", "Hindi"),
        ("{area} میں سیوریج کا پانی گلی میں بھرا ہوا ہے", "Urdu"),
    ],
    "drainage_improvement": [
        ("Even a short shower leaves knee-deep water standing in {area}", "English"),
        ("There is no outflow in {area}, so rain collects for days", "English"),
        ("Water does not drain from our stretch in {area} and mosquitoes have bred", "English"),
        ("{area} me halki barish me hi paani bhar jata hai", "English"),
        ("{area} में बारिश का पानी कई दिनों तक भरा रहता है", "Hindi"),
    ],
    "manhole_repair": [
        ("A manhole on our busy lane in {area} has been left uncovered", "English"),
        ("The chamber lid in {area} is broken and someone will fall in", "English"),
        ("{area} me manhole ka dhakkan toota hua hai, bahut khatarnak hai", "English"),
        ("{area} में मैनहोल खुला पड़ा है", "Hindi"),
    ],
    "sanitation_works": [
        ("Our ward in {area} has no usable public convenience", "English"),
        ("The community toilet in {area} has been unusable for months", "English"),
        ("{area} me public toilet ki halat bahut kharab hai", "English"),
    ],

    # --- Roads and footpaths -> Nagar Nigam ---------------------------------
    "road_repair": [
        ("Our street in {area} is full of craters after the rain", "English"),
        ("The surface in {area} has broken up so badly that two-wheelers skid", "English"),
        ("Deep holes have opened on the approach road in {area}", "English"),
        ("The lane in {area} has not been resurfaced in years and is unusable", "English"),
        ("{area} ki sadak tooti hui hai, chalna mushkil ho gaya hai", "English"),
        ("{area} me gaddhe itne bade hain ki gaadi nikalna mushkil hai", "English"),
        ("{area} में सड़क पूरी तरह टूट चुकी है", "Hindi"),
        ("{area} میں سڑک بالکل ٹوٹ گئی ہے", "Urdu"),
    ],
    "footpath_repair": [
        ("The walkway slabs in {area} are cracked and lifted", "English"),
        ("Pedestrians in {area} have to step onto the carriageway because the path is broken", "English"),
        ("{area} me footpath toot gaya hai, paidal chalna mushkil hai", "English"),
        ("{area} में फुटपाथ टूटा हुआ है", "Hindi"),
    ],
    "bridge_repair": [
        ("The flyover approach in {area} has developed wide cracks", "English"),
        ("The culvert in {area} has sunk on one side and feels unsafe", "English"),
        ("{area} ka pul kamzor ho gaya hai, marammat chahiye", "English"),
    ],

    # --- Waste and animals ---------------------------------------------------
    "garbage_collection": [
        ("Refuse has been piling up at the corner of our lane in {area} for a fortnight", "English"),
        ("The collection van has not come to {area} in ten days", "English"),
        ("The bin near our gate in {area} is overflowing and dogs scatter it", "English"),
        ("A heap of waste has become permanent on the empty plot in {area}", "English"),
        ("{area} me kachra uthaane wali gaadi kai din se nahi aayi", "English"),
        ("{area} में कूड़े का ढेर लगा हुआ है", "Hindi"),
    ],
    "carcass_removal": [
        ("A dead animal has been lying on our road in {area} for two days", "English"),
        ("The stench from a carcass near the drain in {area} is unbearable", "English"),
        ("{area} me mara hua janwar pada hai, badbu fail rahi hai", "English"),
    ],
    "stray_animal_control": [
        ("Packs of stray dogs in {area} chase schoolchildren every morning", "English"),
        ("Cattle sit in the middle of the road in {area} and block traffic", "English"),
        ("{area} me aawara kutton ka bahut aatank hai", "English"),
    ],

    # --- Parks and green space ----------------------------------------------
    "park_maintenance": [
        ("The park in {area} has been left untended and the paths are overrun", "English"),
        ("Nobody has maintained the garden in {area} for a long time", "English"),
        ("{area} ka park bilkul bekaar halat me hai", "English"),
    ],
    "park_equipment_repair": [
        ("The swings in the children's park in {area} are broken and have sharp edges", "English"),
        ("Play equipment in {area} is rusted through and unsafe for small children", "English"),
        ("{area} ke park me jhule toot gaye hain", "English"),
    ],
    "vegetation_clearance": [
        ("Wild growth has taken over the open plot beside our houses in {area}", "English"),
        ("Bushes along the road in {area} have grown so thick they hide the footpath", "English"),
        ("{area} me jhaad jhankaar bahut badh gaye hain", "English"),
    ],
    "park_development": [
        ("There is no green space at all for families in {area}", "English"),
        ("We would like a small park developed on the vacant land in {area}", "English"),
    ],
    "sports_facility_construction": [
        ("Young people in {area} have nowhere to play and need a proper ground", "English"),
        ("A sports facility would help the children of {area} a great deal", "English"),
    ],

    # --- Transport and enforcement ------------------------------------------
    "traffic_signal_repair": [
        ("The signal at our crossing in {area} has been dead for weeks", "English"),
        ("Lights at the {area} intersection are not working and vehicles cut across", "English"),
        ("{area} ke chauraahe ki traffic light band hai", "English"),
    ],
    "bus_stop_construction": [
        ("Commuters in {area} wait in the sun because the stop has no shelter", "English"),
        ("There is no covered waiting area at the stop in {area}", "English"),
        ("{area} me bus stop par chhat nahi hai", "English"),
    ],
    "unauthorized_parking": [
        ("Vehicles are parked across the whole lane in {area} leaving no room to pass", "English"),
        ("Cars block our gate in {area} every evening", "English"),
        ("{area} me log sadak par hi gaadi khadi kar dete hain", "English"),
    ],
    "illegal_encroachment": [
        ("A structure is being raised on our lane in {area} without any sanction", "English"),
        ("Someone has put up construction in {area} with no approved plan", "English"),
        ("Shops have encroached on the public land in {area} and taken over the footpath", "English"),
        ("An unauthorised stand has come up on the road in {area}", "English"),
        ("{area} me bina permission building ban rahi hai", "English"),
        ("{area} में अवैध निर्माण हो रहा है", "Hindi"),
    ],
    "police_patrolling": [
        ("There is no night patrolling in {area} and thefts have increased", "English"),
        ("Residents of {area} do not see a patrol for weeks at a time", "English"),
    ],

    # --- Social infrastructure ----------------------------------------------
    "school_construction": [
        ("Children in {area} travel a long way because there is no school nearby", "English"),
        ("We need a primary school built for the families of {area}", "English"),
    ],
    "hospital_construction": [
        ("There is no health facility within reach of {area}", "English"),
        ("A dispensary in {area} would save residents a long trip for basic care", "English"),
    ],
}


# --- Priority: the band the base model never emits ---------------------------
# The base model collapses Critical into High. These are explicit hazard
# framings, each one a situation where delay risks a life rather than causing
# inconvenience -- which is the distinction the eval's priority cases assert.
# Labelled (text, tag, department, priority, category).

CRITICAL_CASES = [
    ("A snapped power line is lying across the footpath where children pass in {area}",
     "electrical_hazard", "LESA", "Critical"),
    ("An electric pole in {area} is sparking right beside the school entrance",
     "electrical_hazard", "LESA", "Critical"),
    ("A live wire has fallen into the standing water in our lane in {area}",
     "electrical_hazard", "LESA", "Critical"),
    ("Smoke and sparks are coming from the transformer next to the children's park in {area}",
     "electrical_hazard", "LESA", "Critical"),
    ("{area} में स्कूल के गेट के पास ट्रांसफार्मर से चिंगारी निकल रही है",
     "electrical_hazard", "LESA", "Critical"),
    ("{area} me bijli ka taar tootkar raste par gir gaya hai jahan bachche chalte hain",
     "electrical_hazard", "LESA", "Critical"),
    ("The boundary wall of the primary school in {area} has given way",
     "school_construction", "Nagar Nigam", "Critical"),
    ("A portion of an occupied building in {area} has collapsed onto the lane",
     "illegal_encroachment", "LDA", "Critical"),
    ("An uncovered manhole on the busiest crossing in {area} has already injured a rider",
     "manhole_repair", "Nagar Nigam", "Critical"),
    ("Sewage has entered the drinking water line in {area} and people are falling ill",
     "water_quality", "Jal Sansthan", "Critical"),
    ("The culvert in {area} has cracked right through and could give way under traffic",
     "bridge_repair", "Nagar Nigam", "Critical"),
    ("A gas leak smell is spreading from the broken drain beside the market in {area}",
     "sewage_overflow", "Jal Sansthan", "Critical"),
]

# The contrast cases. Without these, teaching Critical risks pushing everything
# upward; a comfort request must stay Low.
LOW_CASES = [
    ("It would be pleasant to have a few more benches in the park at {area}",
     "park_maintenance", "Nagar Nigam", "Low"),
    ("Some shade trees along the walkway in {area} would be a nice addition",
     "vegetation_clearance", "Nagar Nigam", "Low"),
    ("Residents of {area} would appreciate a notice board near the community hall",
     "park_development", "Nagar Nigam", "Low"),
    ("A coat of paint on the park railings in {area} would look better",
     "park_maintenance", "Nagar Nigam", "Low"),
    ("It would be good to have one more dustbin at the corner in {area}",
     "garbage_collection", "Nagar Nigam", "Low"),
    ("{area} ke park me do char bench aur lag jaye to accha rahega",
     "park_maintenance", "Nagar Nigam", "Low"),
]

AREAS = ("Hazratganj", "Gomti Nagar", "Indira Nagar", "Aliganj", "Chowk", "Aminabad",
         "Alambagh", "Rajajipuram", "Mahanagar", "Ashiyana", "Chinhat", "Jankipuram")


def phrasing_count() -> int:
    return sum(len(v) for v in PHRASINGS.values())
