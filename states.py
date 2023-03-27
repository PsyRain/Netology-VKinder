# см. https://github.com/pytransitions/transitions
states = ["START", "SET_GENDER", "SET_MIN_AGE", "SET_MAX_AGE", "SET_CITY", "SEARCHING", "SHOW_RESULTS", "FINISH"]
transitions = [
    {"trigger": "start_and_choose_gender", "source": "START", "dest": "SET_GENDER"},
    {"trigger": "choose_min_age", "source": "SET_GENDER", "dest": "SET_MIN_AGE"},
    {"trigger": "choose_max_age", "source": "SET_MIN_AGE", "dest": "SET_MAX_AGE"},
    {"trigger": "choose_city", "source": "SET_MAX_AGE", "dest": "SET_CITY"},
    {"trigger": "start_search", "source": "SET_CITY", "dest": "SEARCHING", "after": "create_db"},
    {"trigger": "show_results", "source": "SEARCHING", "dest": "SHOW_RESULTS"},
    {"trigger": "next_profile", "source": "SHOW_RESULTS", "dest": None},
    {"trigger": "finish", "source": "*", "dest": "FINISH"},
    {"trigger": "to_start", "source": "*", "dest": "START"},
]