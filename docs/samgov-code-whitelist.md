# SAM.gov Opportunity Filter — Code Whitelist for Review

**Prepared for:** Leadership review
**Prepared by:** Federico Aguilar
**Date:** 2026-08-28
**Source of truth:** `scrapers/samgov.py`
**Code labels decoded from:** 2022 NAICS (US Census Bureau) and the Product & Service Code Manual, April 2025 (acquisition.gov)

---

## What needs review

Every federal solicitation posted to SAM.gov carries two classification codes assigned by the buying agency: a NAICS code (what industry the work belongs to) and a PSC code, also called a Classification Code (what is being bought). About 2,280 posts per day.

We keep a whitelist of these codes. A notice whose code is on the whitelist advances for review; one whose code is not is dropped, unless it is rescued by product language (explained in Section 3).

Three questions:

1. Are the codes on this list the right ones?
2. Are there codes missing that we should be watching?
3. Should any of the broad categories be narrowed? Several currently admit large volumes of work.

---

## How the filter works

The codes are stage one of three. A code being on the list does not mean we bid the work — it means the AI review gets to see it.

| Stage | What it does | Per business day |
|---|---|---|
| Notice type | Keeps open solicitations, drops awards already made | 2,040 to 1,269 |
| **Code whitelist + product language** | The subject of this document | 1,269 to **199** |
| AI review | Reads the scope, judges fit against our product lines | 199 to a handful |

Measured over eight complete business days of the live feed. Volume collapses to near zero on weekends, so these are business-day averages.

**PSC decides. NAICS is a fallback.** PSC says what is being bought; NAICS says what industry the seller is in. When they disagree, PSC is right — a plumbing contractor's chiller replacement carries NAICS 238220, which is on our list, and PSC J041, which is not. NAICS is therefore only consulted when the agency leaves PSC blank. That has not happened once in 14,137 notices.

---

## Section 1 — PSC whitelist (7 prefixes, 365 codes)

This is the gate that does the work.

PSC codes are hierarchical: `Y1` is a category, `Y1BE` is a specific service inside it. We match on prefix, so whitelisting `63` admits every code beginning with 63.

| Prefix | Official category | Codes admitted | Why |
|---|---|---|---|
| Y1 | Construction of Buildings | 115 | New building construction |
| Z1 | Maintenance, Alteration, Repair — Buildings | 115 | Building renovation |
| Z2 | Maintenance, Alteration, Repair — Non-Buildings | 105 | Non-building repair |
| 56 | Construction and Building Material | 10 | To catch **5660**, fencing and gates |
| 71 | Furniture | 5 | To catch **7110**, **7125**, **7195** |
| 99 | Miscellaneous | 9 | To catch **9905**, signs and displays |
| 63 | Alarm, Signal, Security Detection | 6 | To catch **6350** — see Question 1 |

### 1a. The four product prefixes, expanded

Codes marked **Target** are why we whitelisted the prefix. The rest are admitted as a side effect.

**56 — Construction and Building Material**

| Code | Label | Status |
|---|---|---|
| 5610 | Mineral Construction Materials, Bulk | Rides along |
| 5620 | Tile, Brick, and Block | Rides along |
| 5630 | Pipe and Conduit, Nonmetallic | Rides along |
| 5640 | Wallboard, Building Paper, and Thermal Insulation Materials | Rides along |
| 5650 | Roofing and Siding Materials | Rides along |
| 5660 | Fencing, Fences, Gates and Components | **Target** |
| 5670 | Building Components, Prefabricated | Rides along |
| 5675 | Nonwood Construction Lumber and Related Materials | Rides along |
| 5680 | Miscellaneous Construction Materials | Rides along |

**71 — Furniture**

| Code | Label | Status |
|---|---|---|
| 7105 | Household Furniture | Rides along |
| 7110 | Office Furniture | **Target** |
| 7125 | Cabinets, Lockers, Bins, and Shelving | **Target** |
| 7195 | Miscellaneous Furniture and Fixtures | **Target** |

**99 — Miscellaneous**

| Code | Label | Status |
|---|---|---|
| 9905 | Signs, Advertising Displays, and Identification Plates | **Target** |
| 9910 | Jewelry | Rides along |
| 9915 | Collectors and/or Historical Items | Rides along |
| 9920 | Smokers Articles and Matches | Rides along |
| 9925 | Ecclesiastical Equipment, Furnishings, and Supplies | Rides along |
| 9930 | Memorials; Cemeterial and Mortuary Equipment and Supplies | Rides along |
| 9998 | Non-Food Items for Resale | Rides along |
| 9999 | Miscellaneous Items | Rides along |

**63 — Alarm, Signal, Security Detection**

| Code | Label | Status |
|---|---|---|
| 6310 | Traffic and Transit Signal Systems | Rides along |
| 6320 | Shipboard Alarm and Signals Systems | Rides along |
| 6330 | Railroad Signal and Warning Devices | Rides along |
| 6340 | Aircraft Alarm and Signal Systems | Rides along |
| 6350 | Miscellaneous Alarm, Signal, and Security Detection Systems | **Target** |

Of the 30 codes these four prefixes admit, 5 are the ones we wanted. We accept jewelry, memorials, roofing materials and shipboard alarms in order to catch fences, furniture, signs and security systems.

### 1b. Y1 / Z1 / Z2 — construction and renovation

These three are 335 of the 365 codes. They share one structure: the same facility types repeated three times, once for construction, once for maintenance, once for repair or alteration.

| Family | Facility types | Types | Plausible Lavi content |
|---|---|---|---|
| A | Office buildings, conference facilities, administrative buildings | 3 | **High** — lobbies, reception, visitor queuing |
| B | Air traffic control towers, radar facilities, runways, **airport terminals**, communications facilities | 8 | **High** — terminal passenger flow is a core market |
| C | Schools and other educational buildings | 2 | Medium |
| D | Hospitals, infirmaries, laboratories, clinics | 3 | **High** — patient check-in and waiting areas |
| E | Ammunition, maintenance, production, ship repair, tank automotive, industrial | 6 | Low |
| F | Family housing, recreational, troop housing, **dining facilities**, religious, penal | 7 | Mixed — dining and penal queue, housing does not |
| G | Ammunition, food, fuel and open storage, warehouses | 5 | Low |
| H | Government R&D facilities and environmental laboratories | 4 | Low |
| J | **Museums and exhibition buildings**, testing and measurement, miscellaneous | 3 | **High** — museums are a crowd-control market |
| K | Dams, canals, mine fire control, mine subsidence, reclamation, dredging | 7 | **None** |
| L | Airport service roads, highways, bridges, railways, tunnels, **parking** | 4 | Low — parking only |
| M | Electric power generation — coal, gas, geothermal, hydro, nuclear, petroleum, solar, wind, transmission | 9 | **None** |
| N | Fuel supply, heating and cooling plants, pollution abatement, sewage, water supply | 6 | **None** |
| P | Recreation facilities, **exhibit design**, unimproved land, waste treatment | 5 | Mixed — exhibit design only |
| Q | Restoration of real property | 1 | Medium |

*SAM.gov carries a legacy numeric version of each of these (Y111, Z121) alongside the current letter version (Y1AA, Z1BA). The prefix match admits both, which is why the code counts are roughly double the facility list.*

Families K, M and N are 22 facility types across three prefixes — roughly 66 codes — with no plausible content for us. These are the dam repairs and sewer line replacements that reach the AI review and get rejected. They cost review time, not lost leads.

---

## Section 2 — NAICS whitelist (15 codes, fallback only)

**These fifteen codes are effectively inactive.** NAICS is consulted only when PSC is blank, and PSC was populated on all 14,137 notices sampled. The list is kept so that a notice with no PSC still has a code signal.

| # | Code | Official NAICS label |
|---|---|---|
| 1 | 236220 | Commercial and Institutional Building Construction |
| 2 | 237990 | Other Heavy and Civil Engineering Construction |
| 3 | 238190 | Other Foundation, Structure, and Building Exterior Contractors |
| 4 | 238210 | Electrical Contractors and Other Wiring Installation Contractors |
| 5 | 238220 | Plumbing, Heating, and Air-Conditioning Contractors |
| 6 | 238290 | Other Building Equipment Contractors |
| 7 | 238390 | Other Building Finishing Contractors |
| 8 | 238990 | All Other Specialty Trade Contractors |
| 9 | 332323 | Ornamental and Architectural Metal Work Manufacturing |
| 10 | 337127 | Institutional Furniture Manufacturing |
| 11 | 337215 | Showcase, Partition, Shelving, and Locker Manufacturing |
| 12 | 339950 | Sign Manufacturing |
| 13 | 423210 | Furniture Merchant Wholesalers |
| 14 | 488119 | Other Airport Operations |
| 15 | 561621 | Security Systems Services (except Locksmiths) |

### Why NAICS was demoted

It used to accept notices on its own, in parallel with PSC. That admitted **552 notices** whose PSC was not whitelisted. Of those 552, **four** contained any Lavi product language, and all four are recovered by the product-language rescue anyway.

What the other 548 were:

| | |
|---|---|
| 205 | NAICS 238220 — plumbing and HVAC |
| 115 | NAICS 561621 — security systems |
| 101 | NAICS 238210 — electrical |
| 131 | the remaining twelve codes |

By PSC — what the agency said it was actually buying — 399 of the 552 were equipment repair (`J0`) or equipment installation (`N0`). Chiller coils, water softeners, reverse osmosis systems, HVAC units.

Removing that path cut AI review volume from 246 to 199 notices per business day, a 19% reduction, with no measurable loss.

---

## Section 3 — What the codes do not cover

Two product lines are invisible to code matching and are caught by product language instead.

### Electronic and virtual queuing

Agencies file this under information-technology codes. An audit of 29 real queuing solicitations found the code whitelist alone dropped **24 of them**, including a sole-source notice for our own Qtrac.

Whitelisting the IT codes would pull in the entire federal IT pipeline, so they are deliberately excluded:

| Code | Label |
|---|---|
| NAICS 541511 | Custom Computer Programming Services |
| NAICS 541519 | Other Computer Related Services |
| NAICS 334118 | Computer Terminal and Other Computer Peripheral Equipment Manufacturing |
| NAICS 513210 | Software Publishers |
| NAICS 518210 | Computing Infrastructure Providers, Data Processing, Web Hosting |
| PSC 70 | Information Technology Equipment, Software, Supplies and Support Equipment |
| PSC 7A20 | Application development software, perpetual license |
| PSC 7E20 | End user client computing hardware, software and equipment |
| PSC DA01 | Support services for application development |

These are caught instead by narrow terms: *queue management system*, *virtual queuing*, *patient queuing*, *Qtrac*, *Q-Flow*, *Qmatic*, *take-a-number*, *check-in kiosk*, *now serving display*, *patient flow*, *visitor management system*.

### Physical product scattered across unrelated codes

The same problem. Fourteen turnstile-adjacent notices carried **eleven different NAICS values and eleven different PSC values**, several blank. No whitelist can enumerate that. These are caught by terms such as *wayfinding*, *signage*, *stanchion*, *handrail*, *crowd control*, *retractable belt*, *barrier gate*, *slatwall*, *gondola*.

### A note on vocabulary

Federal procurement overloads our product words. "Stanchion" usually means a ship's deck railing post. "Handrail" is often a grab rail on an armored vehicle. "Sneeze guard" is usually cafeteria equipment.

We used to filter these out by domain. That was removed on 2026-08-28: across 18 days it rejected 11 notices, all of them obvious junk, and the AI review rejects them just as reliably. It is not a gap — it is one less thing to maintain.

---

## Section 4 — Open questions

### Question 1: Do we keep PSC prefix 63?

It was added on the belief that we sell turnstiles and powered entry control. **That belief was wrong.** Our crowd-control line is passive barriers only — no turnstiles, optical turnstiles, speed gates or motorized entry gates. Our barrier gates are hinged manual openings in post-and-panel runs.

Turnstile notices still arrive through this prefix and are rejected downstream.

- **Keep** if we want visibility into security-systems work where a barrier gate may be part of a wider lobby scope.
- **Drop** if that visibility is not worth the review volume.

NAICS 561621 was added on the same wrong assumption. It no longer matters either way, since NAICS is now a fallback that never fires, but it should come off the list if the answer here is "drop."

**This is a business decision, not a technical one.**

### Question 2: Should the broad prefixes be narrowed?

Three candidates, in order of how safe they are:

| Change | Effect |
|---|---|
| `99` → `9905` only | Drops jewelry, memorials, smokers articles, ecclesiastical supplies. No loss |
| `63` → `6350` only | Drops shipboard, railroad and aircraft alarm systems. No loss |
| `56` → `5660` (optionally plus `5670`) | Drops roofing, pipe, tile, lumber. No loss |

Excluding the K, M and N families from Y1 / Z1 / Z2 — dams, power generation, utilities — is a larger change and worth discussing before acting.

### Question 3: Are there codes missing?

Measured, not guessed. Over 18 days, **27 distinct PSC codes that are not whitelisted carried Lavi product or queuing language**, across 46 notices. They are extremely scattered — 1.7 notices per code — which is why the product-language rescue exists rather than a longer whitelist.

The ones worth discussing:

| PSC | Label | Seen | Example |
|---|---|---|---|
| T099 / T001 | Printing and Publication Services; Arts and Graphics Services | 6 | "Interactive Wayfinding Maps Service", "Wayfinding Solutions" |
| X1AA | Lease/Rental of Office Buildings | 5 | "Armed Forces Career Center" |
| 9330 | Plastics Fabricated Materials | 3 | "HDPE Board for Signs" |
| 4240 | Safety and Rescue Equipment | 2 | "Weighed System Railings" |
| 7D20 | Hardware and software for delivery processes | 1 | "New Patient Queuing Kiosk" |

**T001 and T099 are the strongest candidate.** Wayfinding is filed as a graphics or publishing service more often than as signage, and those are real opportunities.

All of these already reach the AI review through the language rescue, so adding them would not surface new leads — it would only make the filter less dependent on a notice using our words. That is worth something, but it is not urgent.

### Question 4: Is there a market we serve that is not represented here?

The list was built from our known product lines: crowd control and barriers, railings, signage and wayfinding, store fixtures, institutional furniture, electronic queuing. If a buyer type, facility type or product line is missing from that set, the whitelist will not find it, because nobody looked for it.

---

## Appendix — Where this lives

| Item | Location |
|---|---|
| Live whitelist definitions | `scrapers/samgov.py` |
| NAICS code-to-label map (2,122 entries) | `data/naics_codes.json` |
| PSC code-to-label map (3,837 entries) | `data/psc_codes.json` |
| Script that regenerates both maps from official sources | `scripts/build_code_lookups.py` |

Any change agreed from this review is a change to `scrapers/samgov.py` and takes effect on the next daily run.
