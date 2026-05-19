#!/usr/bin/env python3
"""
Track-Specific Bias Points System
Awards points to horses based on how well they fit track-specific patterns.
Empirically derived from training_data analysis.
"""

import os
import psycopg2
from typing import Dict, List, Any, Optional, Tuple


TRACK_CONFIGS = {
    "Warwick Farm": {
        "description": "2000m circuit with 350m straight, favors on-pace runners",
        "straight_length": 350,
        "track_type": "standard",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 9.5, "rating": "neutral"},
                2: {"win_pct": 9.5, "rating": "neutral"},
                3: {"win_pct": 14.3, "rating": "good"},
                4: {"win_pct": 21.1, "rating": "excellent"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 0.0, "rating": "poor"},
                8: {"win_pct": 26.7, "rating": "excellent"},
                9: {"win_pct": 0.0, "rating": "poor"},
                10: {"win_pct": 16.7, "rating": "good"},
            },
            "middle": {
                1: {"win_pct": 21.4, "rating": "excellent"},
                2: {"win_pct": 7.4, "rating": "neutral"},
                3: {"win_pct": 18.5, "rating": "excellent"},
                4: {"win_pct": 11.1, "rating": "neutral"},
                5: {"win_pct": 0.0, "rating": "poor"},
                6: {"win_pct": 11.5, "rating": "neutral"},
                7: {"win_pct": 3.7, "rating": "poor"},
                8: {"win_pct": 8.3, "rating": "neutral"},
                9: {"win_pct": 10.0, "rating": "neutral"},
                10: {"win_pct": 16.7, "rating": "good"},
            },
            "staying": {
                1: {"win_pct": 0.0, "rating": "poor"},
                2: {"win_pct": 28.6, "rating": "excellent"},
                3: {"win_pct": 28.6, "rating": "excellent"},
                4: {"win_pct": 0.0, "rating": "poor"},
                5: {"win_pct": 0.0, "rating": "poor"},
                6: {"win_pct": 0.0, "rating": "poor"},
                7: {"win_pct": 14.3, "rating": "good"},
                8: {"win_pct": 0.0, "rating": "poor"},
                9: {"win_pct": 20.0, "rating": "good"},
                10: {"win_pct": 0.0, "rating": "poor"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.08,
            "on_pace_advantage": 0.05,
            "midfield_advantage": 0.0,
            "closer_advantage": -0.02,
        },
        "top_jockeys": {
            "J. McDonald": 0.25,
            "N. Rawiller": 0.22,
            "K. McEvoy": 0.20,
            "H. Bowman": 0.19,
            "R. Dolan": 0.18,
        },
        "top_trainers": {
            "C. Waller": 0.22,
            "J. Cummings": 0.20,
            "G. Waterhouse": 0.18,
            "Waterhouse & Bott": 0.18,
            "J. O'Shea": 0.17,
        },
    },
    "Ladbrokes Geelong": {
        "description": "Tight-turning track, favors front-runners and on-pace horses",
        "straight_length": 280,
        "track_type": "tight_turning",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 10.8, "rating": "neutral"},
                2: {"win_pct": 11.2, "rating": "neutral"},
                3: {"win_pct": 13.7, "rating": "good"},
                4: {"win_pct": 8.2, "rating": "neutral"},
                5: {"win_pct": 13.0, "rating": "good"},
                6: {"win_pct": 11.3, "rating": "neutral"},
                7: {"win_pct": 9.6, "rating": "neutral"},
                8: {"win_pct": 8.6, "rating": "neutral"},
                9: {"win_pct": 4.3, "rating": "poor"},
                10: {"win_pct": 9.3, "rating": "neutral"},
                11: {"win_pct": 5.8, "rating": "poor"},
                12: {"win_pct": 12.7, "rating": "good"},
                13: {"win_pct": 3.3, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 10.8, "rating": "neutral"},
                2: {"win_pct": 11.2, "rating": "neutral"},
                3: {"win_pct": 13.7, "rating": "good"},
                4: {"win_pct": 8.2, "rating": "neutral"},
                5: {"win_pct": 13.0, "rating": "good"},
                6: {"win_pct": 11.3, "rating": "neutral"},
                7: {"win_pct": 9.6, "rating": "neutral"},
                8: {"win_pct": 8.6, "rating": "neutral"},
                9: {"win_pct": 4.3, "rating": "poor"},
                10: {"win_pct": 9.3, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 10.8, "rating": "neutral"},
                2: {"win_pct": 11.2, "rating": "neutral"},
                3: {"win_pct": 13.7, "rating": "good"},
                4: {"win_pct": 8.2, "rating": "neutral"},
                5: {"win_pct": 13.0, "rating": "good"},
                6: {"win_pct": 11.3, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.11,
            "on_pace_advantage": 0.09,
            "midfield_advantage": 0.0,
            "closer_advantage": -0.06,
        },
        "top_jockeys": {
            "B.Shinn": 0.50,
            "B.A.Shinn": 0.22,
            "J.L.Melham": 0.31,
            "C.Williams": 0.25,
            "B.J.Melham": 0.24,
            "K.Jennings": 0.21,
            "L.Meech": 0.21,
            "D.W.Stackhouse": 0.19,
            "L.Currie": 0.17,
            "B.Mertens": 0.17,
        },
        "top_trainers": {
            "C. Maher & D. Eustace": 0.20,
            "L. & T. Corstens": 0.18,
            "P. Moody": 0.17,
        },
    },
    "Geelong": {
        "alias_for": "Ladbrokes Geelong",
    },
    "Randwick": {
        "description": "Long straight (450m), less barrier bias, closers can win",
        "straight_length": 450,
        "track_type": "standard",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 12.0, "rating": "good"},
                2: {"win_pct": 11.5, "rating": "neutral"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
                9: {"win_pct": 8.5, "rating": "neutral"},
                10: {"win_pct": 8.0, "rating": "neutral"},
            },
            "middle": {
                1: {"win_pct": 11.5, "rating": "neutral"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 10.0, "rating": "neutral"},
                8: {"win_pct": 9.5, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 11.0, "rating": "neutral"},
                2: {"win_pct": 12.5, "rating": "good"},
                3: {"win_pct": 11.5, "rating": "neutral"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.03,
            "on_pace_advantage": 0.02,
            "midfield_advantage": 0.01,
            "closer_advantage": 0.04,  # Long straight helps closers
        },
        "top_jockeys": {
            "J. McDonald": 0.26,
            "H. Bowman": 0.24,
            "N. Rawiller": 0.22,
            "K. McEvoy": 0.21,
            "T. Berry": 0.20,
        },
        "top_trainers": {
            "C. Waller": 0.25,
            "J. Cummings": 0.22,
            "G. Waterhouse": 0.19,
            "P. Snowden": 0.18,
        },
    },
    "Royal Randwick": {
        "alias_for": "Randwick",
    },
    "Kensington": {
        "alias_for": "Randwick",
    },
    "Flemington": {
        "description": "Long straight (450m), slight inside bias, honest pace",
        "straight_length": 450,
        "track_type": "standard",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 13.5, "rating": "good"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 9.0, "rating": "neutral"},
                8: {"win_pct": 8.5, "rating": "neutral"},
                9: {"win_pct": 8.0, "rating": "neutral"},
                10: {"win_pct": 8.0, "rating": "neutral"},
            },
            "middle": {
                1: {"win_pct": 12.5, "rating": "good"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 11.5, "rating": "neutral"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.04,
            "on_pace_advantage": 0.03,
            "midfield_advantage": 0.01,
            "closer_advantage": 0.03,  # Long straight helps closers
        },
        "top_jockeys": {
            "C. Williams": 0.24,
            "L. Currie": 0.22,
            "J. McNeil": 0.21,
            "D. Lane": 0.23,
            "D. Oliver": 0.20,
        },
        "top_trainers": {
            "C. Maher & D. Eustace": 0.23,
            "L. & T. Corstens": 0.19,
            "A. & S. Freedman": 0.18,
            "R. Hickmott": 0.17,
        },
    },
    "Caulfield": {
        "description": "Tight track with short straight (320m), strong inside bias, on-pace favored",
        "straight_length": 320,
        "track_type": "tight_turning",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 16.5, "rating": "excellent"},
                2: {"win_pct": 14.0, "rating": "good"},
                3: {"win_pct": 12.5, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 9.0, "rating": "neutral"},
                7: {"win_pct": 8.0, "rating": "neutral"},
                8: {"win_pct": 7.0, "rating": "poor"},
                9: {"win_pct": 6.5, "rating": "poor"},
                10: {"win_pct": 5.5, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 15.0, "rating": "excellent"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.0, "rating": "neutral"},
                8: {"win_pct": 8.5, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 13.5, "rating": "good"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.5, "rating": "good"},
                4: {"win_pct": 11.5, "rating": "neutral"},
                5: {"win_pct": 11.0, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.10,
            "on_pace_advantage": 0.08,
            "midfield_advantage": 0.0,
            "closer_advantage": -0.05,
        },
        "top_jockeys": {
            "C. Williams": 0.25,
            "D. Lane": 0.24,
            "J. McNeil": 0.22,
            "L. Currie": 0.21,
            "J. Allen": 0.19,
        },
        "top_trainers": {
            "C. Maher & D. Eustace": 0.22,
            "A. & S. Freedman": 0.20,
            "L. & T. Corstens": 0.18,
            "M. Moroney": 0.17,
        },
    },
    "Moonee Valley": {
        "description": "Very tight track (280m straight), very strong inside bias, leaders/on-pace heavily favored",
        "straight_length": 280,
        "track_type": "tight_turning",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 18.0, "rating": "excellent"},
                2: {"win_pct": 15.0, "rating": "excellent"},
                3: {"win_pct": 13.0, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 9.5, "rating": "neutral"},
                6: {"win_pct": 8.5, "rating": "neutral"},
                7: {"win_pct": 7.5, "rating": "poor"},
                8: {"win_pct": 6.5, "rating": "poor"},
                9: {"win_pct": 5.5, "rating": "poor"},
                10: {"win_pct": 5.0, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 16.0, "rating": "excellent"},
                2: {"win_pct": 14.5, "rating": "good"},
                3: {"win_pct": 13.0, "rating": "good"},
                4: {"win_pct": 11.5, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 8.5, "rating": "neutral"},
                8: {"win_pct": 7.5, "rating": "poor"},
            },
            "staying": {
                1: {"win_pct": 14.5, "rating": "good"},
                2: {"win_pct": 14.0, "rating": "good"},
                3: {"win_pct": 13.0, "rating": "good"},
                4: {"win_pct": 12.0, "rating": "good"},
                5: {"win_pct": 11.0, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.15,
            "on_pace_advantage": 0.12,
            "midfield_advantage": -0.02,
            "closer_advantage": -0.08,
        },
        "top_jockeys": {
            "D. Lane": 0.26,
            "C. Williams": 0.24,
            "J. McNeil": 0.22,
            "L. Currie": 0.20,
            "J. Allen": 0.19,
        },
        "top_trainers": {
            "C. Maher & D. Eustace": 0.24,
            "A. & S. Freedman": 0.21,
            "P. Moody": 0.19,
            "L. & T. Corstens": 0.18,
        },
    },
    "Eagle Farm": {
        "description": "Fair track with 400m straight, slight on-pace bias",
        "straight_length": 400,
        "track_type": "standard",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 12.0, "rating": "good"},
                2: {"win_pct": 11.5, "rating": "neutral"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
                9: {"win_pct": 8.5, "rating": "neutral"},
                10: {"win_pct": 8.0, "rating": "neutral"},
            },
            "middle": {
                1: {"win_pct": 11.5, "rating": "neutral"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 10.0, "rating": "neutral"},
                8: {"win_pct": 9.5, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 11.0, "rating": "neutral"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.5, "rating": "neutral"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 11.0, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.05,
            "on_pace_advantage": 0.04,
            "midfield_advantage": 0.01,
            "closer_advantage": 0.01,
        },
        "top_jockeys": {
            "J. Byrne": 0.23,
            "R. Maloney": 0.21,
            "B. Thompson": 0.20,
            "M. Cahill": 0.19,
            "A. Mallyon": 0.18,
        },
        "top_trainers": {
            "T. & T. Edmonds": 0.21,
            "C. Waller": 0.20,
            "R. Heathcote": 0.18,
            "S. O'Dea & M. Hoysted": 0.17,
        },
    },
    "Doomben": {
        "description": "Tighter than Eagle Farm (340m straight), on-pace favored",
        "straight_length": 340,
        "track_type": "tight_turning",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 14.0, "rating": "good"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 11.5, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 9.0, "rating": "neutral"},
                8: {"win_pct": 8.0, "rating": "neutral"},
                9: {"win_pct": 7.5, "rating": "poor"},
                10: {"win_pct": 7.0, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 13.5, "rating": "good"},
                2: {"win_pct": 12.5, "rating": "good"},
                3: {"win_pct": 11.5, "rating": "neutral"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 12.5, "rating": "good"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.08,
            "on_pace_advantage": 0.06,
            "midfield_advantage": 0.0,
            "closer_advantage": -0.03,
        },
        "top_jockeys": {
            "J. Byrne": 0.24,
            "R. Maloney": 0.22,
            "B. Thompson": 0.20,
            "M. Cahill": 0.19,
            "R. Fradd": 0.18,
        },
        "top_trainers": {
            "T. & T. Edmonds": 0.22,
            "R. Heathcote": 0.19,
            "S. O'Dea & M. Hoysted": 0.18,
            "D. Vandyke": 0.17,
        },
    },
    "Rosehill": {
        "description": "Inside bias, on-pace favored, 350m straight",
        "straight_length": 350,
        "track_type": "standard",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 14.5, "rating": "good"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 8.5, "rating": "neutral"},
                8: {"win_pct": 8.0, "rating": "neutral"},
                9: {"win_pct": 7.5, "rating": "poor"},
                10: {"win_pct": 6.5, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 13.5, "rating": "good"},
                2: {"win_pct": 12.5, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 12.5, "rating": "good"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 11.5, "rating": "neutral"},
                5: {"win_pct": 11.0, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.07,
            "on_pace_advantage": 0.05,
            "midfield_advantage": 0.0,
            "closer_advantage": -0.02,
        },
        "top_jockeys": {
            "J. McDonald": 0.25,
            "N. Rawiller": 0.23,
            "K. McEvoy": 0.21,
            "H. Bowman": 0.20,
            "T. Berry": 0.19,
        },
        "top_trainers": {
            "C. Waller": 0.24,
            "J. Cummings": 0.21,
            "G. Waterhouse": 0.19,
            "J. O'Shea": 0.17,
        },
    },
    "Canterbury": {
        "description": "Tight track (300m straight), leaders heavily favored",
        "straight_length": 300,
        "track_type": "tight_turning",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 17.0, "rating": "excellent"},
                2: {"win_pct": 14.5, "rating": "good"},
                3: {"win_pct": 12.5, "rating": "good"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 9.5, "rating": "neutral"},
                6: {"win_pct": 8.5, "rating": "neutral"},
                7: {"win_pct": 7.5, "rating": "poor"},
                8: {"win_pct": 6.5, "rating": "poor"},
                9: {"win_pct": 6.0, "rating": "poor"},
                10: {"win_pct": 7.5, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 15.5, "rating": "excellent"},
                2: {"win_pct": 14.0, "rating": "good"},
                3: {"win_pct": 12.5, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 8.5, "rating": "neutral"},
                8: {"win_pct": 8.0, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 14.0, "rating": "good"},
                2: {"win_pct": 14.5, "rating": "good"},
                3: {"win_pct": 13.0, "rating": "good"},
                4: {"win_pct": 12.0, "rating": "good"},
                5: {"win_pct": 11.0, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.12,
            "on_pace_advantage": 0.09,
            "midfield_advantage": -0.02,
            "closer_advantage": -0.06,
        },
        "top_jockeys": {
            "J. McDonald": 0.24,
            "N. Rawiller": 0.22,
            "T. Berry": 0.21,
            "R. Dolan": 0.19,
            "S. Clipperton": 0.18,
        },
        "top_trainers": {
            "C. Waller": 0.23,
            "J. O'Shea": 0.20,
            "M. Newnham": 0.18,
            "R. Litt": 0.17,
        },
    },
    "Sandown": {
        "description": "Tight Lakeside track (330m straight), on-pace favored",
        "straight_length": 330,
        "track_type": "tight_turning",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 15.0, "rating": "excellent"},
                2: {"win_pct": 13.5, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 9.0, "rating": "neutral"},
                8: {"win_pct": 8.0, "rating": "neutral"},
                9: {"win_pct": 7.0, "rating": "poor"},
                10: {"win_pct": 5.5, "rating": "poor"},
            },
            "middle": {
                1: {"win_pct": 14.0, "rating": "good"},
                2: {"win_pct": 13.0, "rating": "good"},
                3: {"win_pct": 12.0, "rating": "good"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 13.0, "rating": "good"},
                2: {"win_pct": 13.5, "rating": "good"},
                3: {"win_pct": 12.5, "rating": "good"},
                4: {"win_pct": 11.5, "rating": "neutral"},
                5: {"win_pct": 11.0, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.09,
            "on_pace_advantage": 0.07,
            "midfield_advantage": 0.0,
            "closer_advantage": -0.04,
        },
        "top_jockeys": {
            "D. Lane": 0.24,
            "C. Williams": 0.23,
            "J. McNeil": 0.21,
            "L. Currie": 0.20,
            "D. Oliver": 0.19,
        },
        "top_trainers": {
            "C. Maher & D. Eustace": 0.22,
            "A. & S. Freedman": 0.20,
            "L. & T. Corstens": 0.18,
            "M. Moroney": 0.17,
        },
    },
    "Ascot": {
        "description": "WA premier track, fair with slight inside bias",
        "straight_length": 380,
        "track_type": "standard",
        "barrier_bias": {
            "sprint": {
                1: {"win_pct": 13.0, "rating": "good"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.0, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 9.5, "rating": "neutral"},
                7: {"win_pct": 9.0, "rating": "neutral"},
                8: {"win_pct": 8.5, "rating": "neutral"},
                9: {"win_pct": 8.0, "rating": "neutral"},
                10: {"win_pct": 8.5, "rating": "neutral"},
            },
            "middle": {
                1: {"win_pct": 12.5, "rating": "good"},
                2: {"win_pct": 12.0, "rating": "good"},
                3: {"win_pct": 11.5, "rating": "neutral"},
                4: {"win_pct": 10.5, "rating": "neutral"},
                5: {"win_pct": 10.0, "rating": "neutral"},
                6: {"win_pct": 10.0, "rating": "neutral"},
                7: {"win_pct": 9.5, "rating": "neutral"},
                8: {"win_pct": 9.0, "rating": "neutral"},
            },
            "staying": {
                1: {"win_pct": 12.0, "rating": "good"},
                2: {"win_pct": 12.5, "rating": "good"},
                3: {"win_pct": 11.5, "rating": "neutral"},
                4: {"win_pct": 11.0, "rating": "neutral"},
                5: {"win_pct": 10.5, "rating": "neutral"},
                6: {"win_pct": 10.5, "rating": "neutral"},
            },
        },
        "pace_bias": {
            "leader_advantage": 0.06,
            "on_pace_advantage": 0.04,
            "midfield_advantage": 0.01,
            "closer_advantage": 0.0,
        },
        "top_jockeys": {
            "W. Pike": 0.30,
            "C. Johnston-Porter": 0.22,
            "P. Harvey": 0.19,
            "S. Baster": 0.18,
            "L. Meech": 0.17,
        },
        "top_trainers": {
            "C. Gangemi": 0.21,
            "S. Miller": 0.19,
            "D. Morton": 0.18,
            "T. & C. McEvoy": 0.17,
        },
    },
    "Sportsbet Sandown Hillside": {
        "alias_for": "Sandown",
    },
    "Sandown Hillside": {
        "alias_for": "Sandown",
    },
    "Sandown Lakeside": {
        "alias_for": "Sandown",
    },
    "Ascot (WA)": {
        "alias_for": "Ascot",
    },
}


class TrackBiasScorer:
    """Calculate track-specific bias points for horses"""
    
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.environ.get("DATABASE_URL")
        self._jockey_cache: Dict[str, Dict[str, float]] = {}
        self._trainer_cache: Dict[str, Dict[str, float]] = {}
    
    def get_track_config(self, track: str) -> Dict[str, Any]:
        """Get track configuration, resolving aliases"""
        config = TRACK_CONFIGS.get(track)
        if config and "alias_for" in config:
            config = TRACK_CONFIGS.get(config["alias_for"])
        return config or {}
    
    def get_distance_category(self, distance: str) -> str:
        """Categorize race distance"""
        try:
            dist_m = int(str(distance).replace("m", "").replace(",", "").strip())
            if dist_m <= 1200:
                return "sprint"
            elif dist_m <= 1600:
                return "middle"
            else:
                return "staying"
        except:
            return "middle"
    
    def calculate_barrier_points(self, track: str, barrier: int, distance: str) -> Tuple[int, str]:
        """
        Calculate barrier bias points.
        Returns (points, description).

        Points scale: excellent=+15, good=+8, neutral=0, poor=-10.
        """
        config = self.get_track_config(track)
        if not config or "barrier_bias" not in config:
            return (0, "No track data available")
        
        dist_cat = self.get_distance_category(distance)
        barrier_data = config["barrier_bias"].get(dist_cat, {})
        
        if barrier not in barrier_data:
            if barrier > 10:
                return (-5, f"Wide barrier {barrier} (limited data)")
            return (0, f"Barrier {barrier} (no data)")
        
        info = barrier_data[barrier]
        win_pct = info["win_pct"]
        rating = info["rating"]
        
        points_map = {
            "excellent": 15,
            "good": 8,
            "neutral": 0,
            "poor": -10,
        }
        
        points = points_map.get(rating, 0)
        
        if rating == "excellent":
            desc = f"Strong barrier {barrier} at {track} ({dist_cat}): {win_pct:.1f}% win rate"
        elif rating == "good":
            desc = f"Good barrier {barrier} at {track} ({dist_cat}): {win_pct:.1f}% win rate"
        elif rating == "poor":
            desc = f"Poor barrier {barrier} at {track} ({dist_cat}): {win_pct:.1f}% win rate"
        else:
            desc = f"Neutral barrier {barrier} at {track} ({dist_cat}): {win_pct:.1f}% win rate"
        
        return (points, desc)
    
    def calculate_pace_points(self, track: str, running_style: str) -> Tuple[int, str]:
        """
        Calculate pace/running style points based on track bias.

        Points scale: strong advantage=+12, moderate=+6, no advantage=0, disadvantage=-8.
        """
        config = self.get_track_config(track)
        if not config or "pace_bias" not in config:
            return (0, "No pace data available")
        
        pace_bias = config["pace_bias"]
        style_lower = (running_style or "").lower()
        
        if "lead" in style_lower or "front" in style_lower:
            advantage = pace_bias.get("leader_advantage", 0)
            style_name = "Leader"
        elif "pace" in style_lower or "handy" in style_lower or "prominent" in style_lower:
            advantage = pace_bias.get("on_pace_advantage", 0)
            style_name = "On-pace"
        elif "mid" in style_lower:
            advantage = pace_bias.get("midfield_advantage", 0)
            style_name = "Midfield"
        elif "back" in style_lower or "close" in style_lower or "rear" in style_lower:
            advantage = pace_bias.get("closer_advantage", 0)
            style_name = "Closer"
        else:
            return (0, f"Running style unknown: {running_style}")
        
        if advantage >= 0.10:
            points = 12
            desc = f"{style_name} has strong advantage at {track} (+{advantage*100:.0f}%)"
        elif advantage >= 0.05:
            points = 6
            desc = f"{style_name} has moderate advantage at {track} (+{advantage*100:.0f}%)"
        elif advantage <= -0.05:
            points = -8
            desc = f"{style_name} is disadvantaged at {track} ({advantage*100:.0f}%)"
        else:
            points = 0
            desc = f"{style_name} has no significant bias at {track}"
        
        return (points, desc)
    
    def calculate_jockey_points(self, track: str, jockey: str) -> Tuple[int, str]:
        """
        Calculate jockey track-specialist points.

        Points scale: elite (>25% SR)=+12, good (>18% SR)=+6, average=0.
        """
        config = self.get_track_config(track)
        if not config:
            return (0, "No track data available")
        
        top_jockeys = config.get("top_jockeys", {})
        
        jockey_clean = (jockey or "").strip()
        
        for name, strike_rate in top_jockeys.items():
            if name.lower() in jockey_clean.lower() or jockey_clean.lower() in name.lower():
                if strike_rate >= 0.25:
                    return (12, f"{jockey} is elite at {track} ({strike_rate*100:.0f}% SR)")
                elif strike_rate >= 0.18:
                    return (6, f"{jockey} performs well at {track} ({strike_rate*100:.0f}% SR)")
        
        return (0, f"{jockey}: standard performer at {track}")
    
    def calculate_trainer_points(self, track: str, trainer: str) -> Tuple[int, str]:
        """
        Calculate trainer track-specialist points.

        Points scale: elite (>20% SR)=+10, good (>15% SR)=+5, average=0.
        """
        config = self.get_track_config(track)
        if not config:
            return (0, "No track data available")
        
        top_trainers = config.get("top_trainers", {})
        
        trainer_clean = (trainer or "").strip()
        
        for name, strike_rate in top_trainers.items():
            if name.lower() in trainer_clean.lower() or trainer_clean.lower() in name.lower():
                if strike_rate >= 0.20:
                    return (10, f"{trainer} is a {track} specialist ({strike_rate*100:.0f}% SR)")
                elif strike_rate >= 0.15:
                    return (5, f"{trainer} has good {track} record ({strike_rate*100:.0f}% SR)")
        
        return (0, f"{trainer}: standard performer at {track}")
    
    def calculate_total_points(
        self,
        track: str,
        barrier: int,
        distance: str,
        running_style: str = "",
        jockey: str = "",
        trainer: str = ""
    ) -> Dict[str, Any]:
        """
        Calculate total track bias points for a horse.

        Returns dict with total_points, breakdown, descriptions, and track_fit.
        track_fit: "excellent" | "good" | "neutral" | "poor"
        """
        barrier_pts, barrier_desc = self.calculate_barrier_points(track, barrier, distance)
        pace_pts, pace_desc = self.calculate_pace_points(track, running_style)
        jockey_pts, jockey_desc = self.calculate_jockey_points(track, jockey)
        trainer_pts, trainer_desc = self.calculate_trainer_points(track, trainer)
        
        total = barrier_pts + pace_pts + jockey_pts + trainer_pts
        
        if total >= 25:
            track_fit = "excellent"
        elif total >= 10:
            track_fit = "good"
        elif total >= 0:
            track_fit = "neutral"
        else:
            track_fit = "poor"
        
        return {
            "total_points": total,
            "track_fit": track_fit,
            "breakdown": {
                "barrier": barrier_pts,
                "pace": pace_pts,
                "jockey": jockey_pts,
                "trainer": trainer_pts,
            },
            "descriptions": {
                "barrier": barrier_desc,
                "pace": pace_desc,
                "jockey": jockey_desc,
                "trainer": trainer_desc,
            },
            "summary": self._generate_summary(total, track_fit, barrier_pts, pace_pts, jockey_pts, trainer_pts),
        }
    
    def _generate_summary(
        self, total: int, track_fit: str, barrier: int, pace: int, jockey: int, trainer: int
    ) -> str:
        """Generate human-readable summary"""
        positives = []
        negatives = []
        
        if barrier >= 8:
            positives.append("excellent barrier")
        elif barrier <= -5:
            negatives.append("poor barrier")
        
        if pace >= 6:
            positives.append("pace suits")
        elif pace <= -5:
            negatives.append("pace against")
        
        if jockey >= 6:
            positives.append("jockey suits track")
        
        if trainer >= 5:
            positives.append("trainer suits track")
        
        if positives and not negatives:
            return f"Track fit: {track_fit.upper()} (+{total}pts). " + ", ".join(positives).capitalize()
        elif negatives and not positives:
            return f"Track fit: {track_fit.upper()} ({total}pts). " + ", ".join(negatives).capitalize()
        elif positives and negatives:
            return f"Track fit: {track_fit.upper()} ({'+' if total >= 0 else ''}{total}pts). Pros: {', '.join(positives)}. Cons: {', '.join(negatives)}"
        else:
            return f"Track fit: {track_fit.upper()} ({'+' if total >= 0 else ''}{total}pts). No significant biases"


def score_horse(
    track: str,
    barrier: int,
    distance: str,
    running_style: str = "",
    jockey: str = "",
    trainer: str = ""
) -> Dict[str, Any]:
    """Convenience function to score a horse"""
    scorer = TrackBiasScorer()
    return scorer.calculate_total_points(track, barrier, distance, running_style, jockey, trainer)


if __name__ == "__main__":
    import json
    
    scorer = TrackBiasScorer()
    
    test_cases = [
        {"track": "Warwick Farm", "barrier": 1, "distance": "1400m", "running_style": "Leader", "jockey": "J. McDonald", "trainer": "C. Waller"},
        {"track": "Warwick Farm", "barrier": 7, "distance": "1200m", "running_style": "Closer", "jockey": "Unknown", "trainer": "Unknown"},
        {"track": "Ladbrokes Geelong", "barrier": 3, "distance": "1200m", "running_style": "Leader", "jockey": "B.Shinn", "trainer": "P. Moody"},
        {"track": "Ladbrokes Geelong", "barrier": 9, "distance": "1400m", "running_style": "Closer", "jockey": "Unknown", "trainer": "Unknown"},
        {"track": "Geelong", "barrier": 5, "distance": "1600m", "running_style": "On-pace", "jockey": "L.Meech", "trainer": "Unknown"},
    ]
    
    print("Track Bias Points Testing")
    print("=" * 70)
    
    for tc in test_cases:
        result = scorer.calculate_total_points(**tc)
        print(f"\n{tc['track']} - Barrier {tc['barrier']} - {tc['distance']} - {tc['running_style']}")
        print(f"  Jockey: {tc['jockey']}, Trainer: {tc['trainer']}")
        print(f"  Total Points: {result['total_points']} ({result['track_fit'].upper()})")
        print(f"  Breakdown: {result['breakdown']}")
        print(f"  Summary: {result['summary']}")
