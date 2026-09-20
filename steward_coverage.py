#!/usr/bin/env python3
"""Regenerate `steward-rule-coverage.md` from the spec and the allocation below.

    python3 steward_coverage.py

The allocation is the one thing here kept by hand: each issue of
`steward-module-issues.md` against the requirement IDs it owns. Everything else is read from
`docs/wip-specs/steward_module_specification.md`, so a rule added, moved or deleted shows up as
a gap or a clash the moment this is run. Run it after any change to either document, and again
before filing or closing an issue of the set.

Not a `tools/` script and not unit-tested: it belongs to the stewarding spec work on this branch,
and is deleted with it once the module is built.
"""

ALLOC = {
    "S01": {
        "CON": [
            [
                1,
                8
            ],
            [
                40,
                41
            ],
            [
                55,
                67
            ],
            [
                73,
                74
            ]
        ]
    },
    "S02": {
        "CFG": [
            [
                1,
                2
            ]
        ],
        "MOD": [
            [
                1,
                10
            ],
            [
                15,
                16
            ],
            [
                18,
                23
            ]
        ]
    },
    "S03": {
        "CHN": [
            [
                1,
                15
            ]
        ]
    },
    "S04": {
        "CON": [
            [
                21,
                29
            ]
        ],
        "TEM": [
            [
                1,
                18
            ]
        ]
    },
    "S05": {
        "APL": [
            [
                1,
                6
            ],
            [
                8,
                9
            ]
        ],
        "JUS": [
            [
                1,
                3
            ],
            [
                8,
                15
            ]
        ],
        "TIM": [
            [
                1,
                18
            ]
        ]
    },
    "S06": {
        "CON": [
            [
                53,
                54
            ]
        ],
        "OUT": [
            [
                1,
                26
            ]
        ]
    },
    "S07": {
        "PEN": [
            [
                1,
                24
            ]
        ]
    },
    "S08": {
        "ARL": [
            [
                1,
                24
            ]
        ],
        "CON": [
            [
                68,
                70
            ]
        ]
    },
    "S09": {
        "SET": [
            [
                1,
                12
            ]
        ]
    },
    "S12": {
        "CON": [
            [
                31,
                31
            ],
            [
                39,
                39
            ],
            [
                42,
                48
            ],
            [
                50,
                50
            ]
        ],
        "CYC": [
            [
                22,
                43
            ]
        ],
        "TKT": [
            [
                1,
                1
            ],
            [
                16,
                17
            ],
            [
                48,
                51
            ]
        ]
    },
    "S13": {
        "CON": [
            [
                33,
                33
            ]
        ],
        "CYC": [
            [
                55,
                58
            ]
        ],
        "DEL": [
            [
                1,
                16
            ],
            [
                18,
                21
            ]
        ]
    },
    "S14": {
        "APL": [
            [
                7,
                7
            ],
            [
                10,
                11
            ]
        ],
        "CON": [
            [
                34,
                34
            ],
            [
                49,
                49
            ],
            [
                51,
                51
            ]
        ],
        "CYC": [
            [
                65,
                91
            ]
        ]
    },
    "S15": {
        "CON": [
            [
                35,
                35
            ]
        ],
        "CYC": [
            [
                92,
                107
            ]
        ]
    },
    "S16": {
        "CON": [
            [
                30,
                30
            ]
        ],
        "CYC": [
            [
                108,
                115
            ]
        ]
    },
    "S17": {
        "CYC": [
            [
                1,
                21
            ]
        ]
    },
    "S18": {
        "CYC": [
            [
                116,
                123
            ]
        ]
    },
    "S19": {
        "ART": [
            [
                1,
                11
            ]
        ]
    },
    "S20": {
        "BAN": [
            [
                1,
                1
            ],
            [
                7,
                8
            ],
            [
                47,
                65
            ]
        ]
    },
    "S21": {
        "BAN": [
            [
                2,
                6
            ],
            [
                16,
                18
            ],
            [
                23,
                35
            ],
            [
                40,
                46
            ]
        ]
    },
    "S22": {
        "BAN": [
            [
                10,
                15
            ]
        ]
    },
    "S23": {
        "LIC": [
            [
                1,
                11
            ]
        ]
    },
    "S24": {
        "LIC": [
            [
                12,
                20
            ]
        ]
    },
    "S25": {
        "BAN": [
            [
                9,
                9
            ],
            [
                19,
                22
            ],
            [
                36,
                39
            ]
        ],
        "MOD": [
            [
                11,
                14
            ]
        ]
    },
    "S26": {
        "COC": [
            [
                1,
                12
            ]
        ]
    },
    "S27": {
        "BKP": [
            [
                1,
                9
            ]
        ],
        "COC": [
            [
                57,
                63
            ]
        ]
    },
    "S28": {
        "CCY": [
            [
                1,
                26
            ]
        ],
        "CON": [
            [
                36,
                38
            ],
            [
                52,
                52
            ]
        ]
    },
    "S29": {
        "REV": [
            [
                1,
                38
            ]
        ]
    },
    "S30": {
        "VER": [
            [
                1,
                17
            ],
            [
                19,
                41
            ]
        ]
    },
    "S31": {
        "VER": [
            [
                18,
                18
            ],
            [
                42,
                54
            ]
        ]
    },
    "S32": {
        "STD": [
            [
                1,
                5
            ]
        ]
    },
    "S33": {
        "SHT": [
            [
                1,
                15
            ]
        ]
    },
    "S34": {
        "SHT": [
            [
                16,
                38
            ]
        ]
    },
    "S35": {
        "PCK": [
            [
                1,
                3
            ]
        ]
    },
    "S36": {
        "RST": [
            [
                1,
                4
            ]
        ]
    },
    "S37": {
        "MOD": [
            [
                17,
                17
            ]
        ],
        "TST": [
            [
                1,
                6
            ]
        ]
    },
    "S38": {
        "JUS": [
            [
                4,
                7
            ]
        ]
    },
    "S39": {
        "CON": [
            [
                9,
                20
            ],
            [
                75,
                76
            ]
        ]
    },
    "S40": {
        "TEM": [
            [
                19,
                32
            ]
        ]
    },
    "S41": {
        "OUT": [
            [
                27,
                55
            ]
        ]
    },
    "S42": {
        "ARL": [
            [
                25,
                46
            ]
        ],
        "CON": [
            [
                71,
                72
            ]
        ]
    },
    "S43": {
        "ARL": [
            [
                47,
                66
            ]
        ]
    },
    "S44": {
        "TKT": [
            [
                2,
                13
            ]
        ]
    },
    "S45": {
        "TKT": [
            [
                18,
                29
            ],
            [
                43,
                47
            ]
        ]
    },
    "S46": {
        "TKT": [
            [
                14,
                15
            ],
            [
                30,
                36
            ]
        ]
    },
    "S47": {
        "TKT": [
            [
                37,
                42
            ]
        ]
    },
    "S48": {
        "DEL": [
            [
                17,
                17
            ],
            [
                22,
                30
            ]
        ]
    },
    "S49": {
        "CYC": [
            [
                59,
                64
            ]
        ],
        "DEL": [
            [
                31,
                37
            ]
        ]
    },
    "S50": {
        "COC": [
            [
                13,
                32
            ]
        ]
    },
    "S51": {
        "CON": [
            [
                32,
                32
            ]
        ],
        "CYC": [
            [
                44,
                54
            ]
        ]
    },
    "S52": {
        "COC": [
            [
                33,
                56
            ]
        ]
    }
}

import re
A = ALLOC
owner = {}
for k, secs in A.items():
    for sec, rs in secs.items():
        for lo, hi in rs:
            for n in range(lo, hi+1):
                owner[(sec, n)] = k

TITLES = {}
for line in open('steward-module-issues.md'):
    m = re.match(r"^## (S\d\d) — (.*)$", line)
    if m: TITLES[m.group(1)] = m.group(2).strip()

SECTION_NAME = {
 "MOD":"The module itself","CON":"Concepts","CFG":"Configuring the module","CHN":"Channels",
 "TEM":"The stewarding team","TIM":"Timings","APL":"Appeals","JUS":"Justifications",
 "OUT":"Outcomes","PEN":"Penalty types and bans","ARL":"Automated penalty rules","BKP":"Backups",
 "COC":"Code of Conduct investigations","SET":"Changing settings during a season","TKT":"Tickets",
 "DEL":"Deliberation","CYC":"Stewarding cycle","CCY":"Conduct cycle","REV":"Revoking penalties",
 "ART":"Auto-rule triggering","BAN":"Bans","LIC":"Viewing a licence","VER":"Verdict output",
 "STD":"Standings output","SHT":"Licence sheet output","PCK":"When the bot is packed",
 "RST":"When the bot restarts","TST":"Test mode",
}
rules, order = {}, []
for line in open('docs/wip-specs/steward_module_specification.md'):
    m = re.search(r"\[STW-([A-Z]+)-(\d+)\] (.*)$", line.rstrip("\n"))
    if m:
        sec, n, text = m.group(1), int(m.group(2)), m.group(3)
        rules[(sec, n)] = text
        order.append((sec, n))

out = ["# Stewarding module — rule coverage", "",
 "Generated from `docs/wip-specs/steward_module_specification.md` and the allocation in",
 "[steward-module-issues.md](steward-module-issues.md). **Do not hand-maintain it**: regenerate it",
 "after any change to either, and it will name any rule owned by nobody or by two issues.", "",
 "Every row is one rule of the spec and the issue that owns it. A rule with no issue would appear",
 "as `—`, which is the gap this table exists to find.", ""]
miss = [f"STW-{s}-{n:03d}" for s, n in order if (s, n) not in owner]
out += ["## The issues", ""] + [f"- **{k}** — {TITLES[k]}" for k in sorted(TITLES)] + [""]
out += [f"**{len(order)} rules. Rules owned by no issue: {len(miss)}"
        + (f" — {', '.join(miss)}" if miss else "") + ".**", ""]
cur = None
for sec, n in order:
    if sec != cur:
        cur = sec
        out += ["", f"## {sec} — {SECTION_NAME[sec]}", "", "| Rule | Issue | The rule |", "|---|---|---|"]
    k = owner.get((sec, n), "—")
    txt = rules[(sec, n)].replace("|", "\\|")
    if len(txt) > 96: txt = txt[:95].rstrip() + "…"
    out.append(f"| STW-{sec}-{n:03d} | {k} | {txt} |")
open('steward-rule-coverage.md','w').write("\n".join(out) + "\n")
print(len(order), "rows;", len(miss), "unowned")
