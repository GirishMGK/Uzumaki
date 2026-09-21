"""
Build pin_master_raw.csv and pin_master.parquet from the documented India Post
zone/circle/pincode structure.

Run once:  python -m fcmr_core.reference.generate_pin_master

Source structure follows India Post's official PIN zone assignment:
  Digit 1 = Postal Zone (1-8 civil, 9 military/APO)
  Digits 1-2 = Region / Postal Circle
"""

from __future__ import annotations

import csv
from pathlib import Path

_HERE = Path(__file__).parent

# (pincode_prefix_2digit, state_name, circle_name, sample_districts)
# Each prefix generates representative 6-digit PINs for validation coverage.
_CIRCLE_DATA: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    # Zone 1 – Delhi & surrounding
    (
        "11",
        "delhi",
        "Delhi",
        [
            ("110001", "Central Delhi", "Old Delhi HO"),
            ("110002", "Central Delhi", "Delhi GPO"),
            ("110003", "South Delhi", "Lodhi Road"),
            ("110016", "South Delhi", "Hauz Khas"),
            ("110019", "South East Delhi", "Kalkaji"),
            ("110020", "South Delhi", "Saket"),
            ("110025", "East Delhi", "Vivek Vihar"),
            ("110048", "South Delhi", "Malviya Nagar"),
            ("110054", "North Delhi", "Model Town"),
            ("110059", "West Delhi", "Janakpuri"),
            ("110063", "West Delhi", "Dwarka"),
            ("110092", "East Delhi", "Preet Vihar"),
            ("110096", "East Delhi", "Patparganj"),
        ],
    ),
    (
        "12",
        "haryana",
        "Delhi",
        [
            ("121001", "Faridabad", "Faridabad HO"),
            ("122001", "Gurugram", "Gurgaon HO"),
            ("123001", "Mahendragarh", "Narnaul HO"),
            ("124001", "Jhajjar", "Rohtak HO"),
            ("125001", "Hisar", "Hisar HO"),
            ("126001", "Jind", "Jind HO"),
            ("127001", "Bhiwani", "Bhiwani HO"),
            ("128001", "Charkhi Dadri", "Charkhi Dadri HO"),
            ("129001", "Sonepat", "Sonepat HO"),
            ("131001", "Sonepat", "Sonepat HO"),
            ("132001", "Karnal", "Karnal HO"),
            ("133001", "Ambala", "Ambala HO"),
            ("134001", "Ambala", "Ambala City HO"),
            ("135001", "Yamunanagar", "Yamunanagar HO"),
            ("136001", "Kurukshetra", "Kurukshetra HO"),
        ],
    ),
    (
        "13",
        "punjab",
        "Punjab",
        [
            ("141001", "Ludhiana", "Ludhiana HO"),
            ("143001", "Amritsar", "Amritsar HO"),
            ("144001", "Jalandhar", "Jalandhar HO"),
            ("145001", "Gurdaspur", "Pathankot HO"),
            ("147001", "Patiala", "Patiala HO"),
            ("148001", "Sangrur", "Sangrur HO"),
            ("151001", "Bathinda", "Bathinda HO"),
            ("152001", "Muktsar", "Faridkot HO"),
            ("160001", "Chandigarh", "Chandigarh HO"),
            ("160002", "Chandigarh", "Sector 17"),
        ],
    ),
    (
        "14",
        "himachal pradesh",
        "Punjab",
        [
            ("170001", "Shimla", "Shimla HO"),
            ("175001", "Mandi", "Mandi HO"),
            ("176001", "Kangra", "Dharamsala HO"),
            ("177001", "Hamirpur", "Hamirpur HO"),
            ("173001", "Solan", "Solan HO"),
            ("174001", "Bilaspur", "Bilaspur HO"),
        ],
    ),
    (
        "15",
        "rajasthan",
        "Rajasthan",
        [
            ("302001", "Jaipur", "Jaipur HO"),
            ("303001", "Jaipur", "Amber HO"),
            ("305001", "Ajmer", "Ajmer HO"),
            ("306001", "Pali", "Pali HO"),
            ("311001", "Bhilwara", "Bhilwara HO"),
            ("313001", "Udaipur", "Udaipur HO"),
            ("324001", "Kota", "Kota HO"),
            ("325001", "Bundi", "Bundi HO"),
            ("331001", "Churu", "Churu HO"),
            ("334001", "Bikaner", "Bikaner HO"),
            ("342001", "Jodhpur", "Jodhpur HO"),
            ("344001", "Barmer", "Barmer HO"),
        ],
    ),
    # Zone 2 – Andhra Pradesh / Telangana
    (
        "50",
        "telangana",
        "Andhra Pradesh",
        [
            ("500001", "Hyderabad", "Hyderabad GPO"),
            ("500002", "Hyderabad", "Secunderabad"),
            ("500016", "Hyderabad", "Banjara Hills"),
            ("500018", "Hyderabad", "Jubilee Hills"),
            ("500034", "Hyderabad", "Kukatpally"),
            ("500072", "Hyderabad", "HITECH City"),
            ("500081", "Rangareddy", "Gachibowli"),
            ("501001", "Rangareddy", "Rangareddy HO"),
            ("502001", "Medak", "Medak HO"),
            ("503001", "Nizamabad", "Nizamabad HO"),
            ("504001", "Adilabad", "Adilabad HO"),
            ("505001", "Karimnagar", "Karimnagar HO"),
            ("506001", "Warangal", "Warangal HO"),
            ("507001", "Khammam", "Khammam HO"),
            ("508001", "Nalgonda", "Nalgonda HO"),
            ("509001", "Mahabubnagar", "Mahabubnagar HO"),
        ],
    ),
    (
        "51",
        "andhra pradesh",
        "Andhra Pradesh",
        [
            ("520001", "Krishna", "Vijayawada HO"),
            ("522001", "Guntur", "Guntur HO"),
            ("524001", "Nellore", "Nellore HO"),
            ("530001", "Visakhapatnam", "Visakhapatnam HO"),
            ("534001", "West Godavari", "Eluru HO"),
            ("533001", "East Godavari", "Rajahmundry HO"),
            ("515001", "Anantapur", "Anantapur HO"),
            ("516001", "Kadapa", "Kadapa HO"),
            ("517001", "Chittoor", "Tirupati HO"),
            ("518001", "Kurnool", "Kurnool HO"),
        ],
    ),
    # Zone 3 – Karnataka / Kerala
    (
        "56",
        "karnataka",
        "Karnataka",
        [
            ("560001", "Bangalore Urban", "Bangalore GPO"),
            ("560002", "Bangalore Urban", "Shivajinagar"),
            ("560010", "Bangalore Urban", "Rajajinagar"),
            ("560034", "Bangalore Urban", "Jayanagar"),
            ("560038", "Bangalore Urban", "Koramangala"),
            ("560068", "Bangalore Urban", "BTM Layout"),
            ("560076", "Bangalore Urban", "Whitefield"),
            ("560100", "Bangalore Rural", "Yelahanka"),
            ("562001", "Bangalore Rural", "Doddaballapur HO"),
            ("570001", "Mysuru", "Mysore HO"),
            ("575001", "Dakshina Kannada", "Mangalore HO"),
            ("580001", "Dharwad", "Hubli HO"),
            ("585101", "Kalaburagi", "Gulbarga HO"),
            ("590001", "Belagavi", "Belgaum HO"),
            ("577001", "Davangere", "Davangere HO"),
            ("572101", "Tumkur", "Tumkur HO"),
        ],
    ),
    (
        "67",
        "kerala",
        "Kerala",
        [
            ("682001", "Ernakulam", "Cochin HO"),
            ("682020", "Ernakulam", "Kakkanad"),
            ("683101", "Ernakulam", "Aluva"),
            ("695001", "Thiruvananthapuram", "Trivandrum HO"),
            ("680001", "Thrissur", "Thrissur HO"),
            ("676101", "Malappuram", "Malappuram HO"),
            ("673001", "Kozhikode", "Calicut HO"),
            ("670001", "Kannur", "Kannur HO"),
            ("689101", "Pathanamthitta", "Tiruvalla HO"),
            ("686001", "Kottayam", "Kottayam HO"),
            ("691001", "Kollam", "Kollam HO"),
        ],
    ),
    # Zone 4 – Maharashtra / Gujarat
    (
        "40",
        "maharashtra",
        "Maharashtra",
        [
            ("400001", "Mumbai City", "Mumbai GPO"),
            ("400002", "Mumbai City", "Mumbai CST"),
            ("400005", "Mumbai City", "Colaba"),
            ("400016", "Mumbai City", "Dadar"),
            ("400051", "Mumbai Suburban", "Bandra"),
            ("400063", "Mumbai Suburban", "Goregaon"),
            ("400070", "Mumbai Suburban", "Kurla"),
            ("400076", "Mumbai Suburban", "Powai"),
            ("400078", "Mumbai Suburban", "Vikhroli"),
            ("400101", "Thane", "Thane HO"),
            ("411001", "Pune", "Pune HO"),
            ("411028", "Pune", "Hinjewadi"),
            ("440001", "Nagpur", "Nagpur HO"),
            ("422001", "Nashik", "Nashik HO"),
            ("431001", "Aurangabad", "Aurangabad HO"),
            ("416001", "Kolhapur", "Kolhapur HO"),
            ("444601", "Amravati", "Amravati HO"),
        ],
    ),
    (
        "38",
        "gujarat",
        "Gujarat",
        [
            ("380001", "Ahmedabad", "Ahmedabad GPO"),
            ("380006", "Ahmedabad", "Ellis Bridge"),
            ("380009", "Ahmedabad", "Navrangpura"),
            ("380015", "Ahmedabad", "Satellite"),
            ("382345", "Gandhinagar", "Gandhinagar HO"),
            ("390001", "Vadodara", "Baroda HO"),
            ("395001", "Surat", "Surat HO"),
            ("361001", "Jamnagar", "Jamnagar HO"),
            ("360001", "Rajkot", "Rajkot HO"),
            ("364001", "Bhavnagar", "Bhavnagar HO"),
            ("363001", "Surendranagar", "Surendranagar HO"),
        ],
    ),
    # Zone 6 – Tamil Nadu
    (
        "60",
        "tamil nadu",
        "Tamil Nadu",
        [
            ("600001", "Chennai", "Chennai GPO"),
            ("600002", "Chennai", "Chennai Fort"),
            ("600010", "Chennai", "Nungambakkam"),
            ("600020", "Chennai", "Adyar"),
            ("600045", "Chennai", "Velachery"),
            ("600096", "Chennai", "Sholinganallur"),
            ("600119", "Chennai", "Perumbakkam"),
            ("641001", "Coimbatore", "Coimbatore HO"),
            ("625001", "Madurai", "Madurai HO"),
            ("620001", "Tiruchirappalli", "Trichy HO"),
            ("636001", "Salem", "Salem HO"),
            ("627001", "Tirunelveli", "Tirunelveli HO"),
            ("632001", "Vellore", "Vellore HO"),
            ("605001", "Puducherry", "Pondicherry HO"),
        ],
    ),
    # Zone 7 – West Bengal / Odisha / NE
    (
        "70",
        "west bengal",
        "West Bengal",
        [
            ("700001", "Kolkata", "Kolkata GPO"),
            ("700013", "Kolkata", "Shyambazar"),
            ("700019", "Kolkata", "Ballygunge"),
            ("700029", "Kolkata", "Dhakuria"),
            ("700048", "Kolkata", "New Town"),
            ("700091", "North 24 Parganas", "Barasat"),
            ("700101", "South 24 Parganas", "Jadavpur"),
            ("711101", "Howrah", "Howrah HO"),
            ("712101", "Hooghly", "Hooghly HO"),
            ("713101", "Paschim Bardhaman", "Durgapur HO"),
            ("721101", "West Midnapore", "Midnapore HO"),
            ("741101", "Nadia", "Krishnanagar HO"),
            ("742101", "Murshidabad", "Berhampore HO"),
            ("734001", "Darjeeling", "Darjeeling HO"),
        ],
    ),
    (
        "75",
        "odisha",
        "Odisha",
        [
            ("751001", "Khorda", "Bhubaneswar HO"),
            ("753001", "Cuttack", "Cuttack HO"),
            ("760001", "Ganjam", "Berhampur HO"),
            ("769001", "Jharsuguda", "Jharsuguda HO"),
            ("756001", "Bhadrak", "Bhadrak HO"),
            ("770001", "Sundergarh", "Rourkela HO"),
        ],
    ),
    # Zone 8 – UP / Uttarakhand / MP / Chhattisgarh / Bihar / Jharkhand
    (
        "20",
        "uttar pradesh",
        "Uttar Pradesh",
        [
            ("201001", "Ghaziabad", "Ghaziabad HO"),
            ("208001", "Kanpur Nagar", "Kanpur HO"),
            ("211001", "Prayagraj", "Allahabad HO"),
            ("221001", "Varanasi", "Varanasi HO"),
            ("226001", "Lucknow", "Lucknow HO"),
            ("226010", "Lucknow", "Hazratganj"),
            ("250001", "Meerut", "Meerut HO"),
            ("282001", "Agra", "Agra HO"),
            ("243001", "Bareilly", "Bareilly HO"),
            ("244001", "Amroha", "Moradabad HO"),
            ("273001", "Gorakhpur", "Gorakhpur HO"),
            ("247001", "Saharanpur", "Saharanpur HO"),
        ],
    ),
    (
        "24",
        "uttarakhand",
        "Uttar Pradesh",
        [
            ("248001", "Dehradun", "Dehradun HO"),
            ("249301", "Tehri Garhwal", "Tehri HO"),
            ("263001", "Nainital", "Nainital HO"),
            ("263139", "Nainital", "Haldwani HO"),
            ("246001", "Pauri Garhwal", "Pauri HO"),
            ("251001", "Muzaffarnagar", "Muzaffarnagar HO"),
        ],
    ),
    (
        "46",
        "madhya pradesh",
        "Madhya Pradesh",
        [
            ("462001", "Bhopal", "Bhopal HO"),
            ("452001", "Indore", "Indore HO"),
            ("474001", "Gwalior", "Gwalior HO"),
            ("492001", "Raipur", "Raipur HO"),
            ("490001", "Durg", "Bhilai HO"),
            ("482001", "Jabalpur", "Jabalpur HO"),
            ("456001", "Ujjain", "Ujjain HO"),
            ("470001", "Sagar", "Sagar HO"),
        ],
    ),
    (
        "80",
        "bihar",
        "Bihar",
        [
            ("800001", "Patna", "Patna GPO"),
            ("800004", "Patna", "Patna City"),
            ("800014", "Patna", "Danapur"),
            ("804453", "Nalanda", "Bihar Sharif HO"),
            ("842001", "Muzaffarpur", "Muzaffarpur HO"),
            ("823001", "Gaya", "Gaya HO"),
            ("812001", "Bhagalpur", "Bhagalpur HO"),
            ("845401", "East Champaran", "Motihari HO"),
            ("852101", "Saharsa", "Supaul HO"),
        ],
    ),
    (
        "83",
        "jharkhand",
        "Bihar",
        [
            ("834001", "Ranchi", "Ranchi HO"),
            ("831001", "East Singhbhum", "Jamshedpur HO"),
            ("826001", "Dhanbad", "Dhanbad HO"),
            ("835101", "West Singhbhum", "Chaibasa HO"),
            ("825301", "Hazaribagh", "Hazaribagh HO"),
            ("815301", "Giridih", "Deoghar HO"),
        ],
    ),
]


def build_raw_csv() -> None:
    raw_path = _HERE / "pin_master_raw.csv"
    rows: list[dict] = []

    for prefix2, state, circle, entries in _CIRCLE_DATA:
        for pincode, district, office_name in entries:
            rows.append(
                {
                    "Pincode": pincode,
                    "OfficeName": office_name,
                    "District": district,
                    "StateName": state,
                    "CircleName": circle,
                }
            )

    with raw_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["Pincode", "OfficeName", "District", "StateName", "CircleName"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} PIN entries to {raw_path}")


if __name__ == "__main__":
    build_raw_csv()
    from fcmr_core.reference.pin_master import build_master

    build_master()
    print(f"Built Parquet master at {_HERE / 'pin_master.parquet'}")
