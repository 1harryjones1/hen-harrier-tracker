"""Offline tests for scotland_trials.py (no network). Run: python3 -m unittest -v"""

import datetime as dt
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scotland_trials as st  # noqa: E402

TODAY = dt.date(2026, 11, 3)


class Geography(unittest.TestCase):
    INSIDE = {
        "Edinburgh": (55.95, -3.19), "Glasgow": (55.86, -4.25), "Aberdeen": (57.15, -2.09),
        "Inverness": (57.48, -4.22), "Dumfries": (55.07, -3.60), "Lerwick": (60.15, -1.14),
        "Stornoway": (58.21, -6.39), "Kirkwall": (58.98, -2.96), "Eyemouth": (55.87, -2.09),
        "Stranraer": (54.90, -5.03), "Kelso": (55.60, -2.43), "Annan": (54.99, -3.26),
    }
    OUTSIDE = {
        "Newcastle": (54.97, -1.61), "Carlisle": (54.89, -2.93), "Berwick": (55.77, -2.00),
        "Belfast": (54.60, -5.93), "Derry": (55.00, -7.31), "Douglas IoM": (54.15, -4.48),
        "Workington": (54.64, -3.55), "Silloth": (54.87, -3.39), "Alnwick": (55.41, -1.70),
        "Wooler": (55.55, -2.01), "Coleraine": (55.13, -6.67), "Manchester": (53.48, -2.24),
    }

    def test_inside(self):
        for name, (lat, lon) in self.INSIDE.items():
            self.assertTrue(st.point_in_polygon(lon, lat, st.SCOTLAND_POLYGON), name)

    def test_outside(self):
        for name, (lat, lon) in self.OUTSIDE.items():
            self.assertFalse(st.point_in_polygon(lon, lat, st.SCOTLAND_POLYGON), name)

    def test_place_names(self):
        self.assertTrue(st.place_is_scottish("Edinburgh", None, None))
        self.assertTrue(st.place_is_scottish("Larbert", "", "Forth Valley Royal Hospital"))
        self.assertTrue(st.place_is_scottish("Somewhere", "", "NHS Tayside"))
        self.assertFalse(st.place_is_scottish("Leeds", "West Yorkshire", "St James's"))


class PaySignals(unittest.TestCase):
    def test_amount(self):
        ev, _ = st.pay_signals("Participants will receive £200 for completing the study.")
        self.assertTrue(ev)

    def test_voucher(self):
        ev, _ = st.pay_signals("You will be given a £20 Amazon voucher as a thank you.")
        self.assertTrue(ev)

    def test_expenses_only(self):
        ev, exp = st.pay_signals("Reasonable travel expenses will be paid back to participants.")
        self.assertEqual(ev, [])
        self.assertTrue(exp)

    def test_false_positives(self):
        for text in [
            "Samples are returned using the pre-paid and addressed postage box.",
            "The study is paid for by the NIHR.",
            "Exclusion: participants weighing less than 110 pounds.",
            "Exclusion: participants in paid employment for more than 30 hours.",
            "Patients will use an incentive spirometer after surgery.",
            "Particular attention will be paid to adverse events.",
        ]:
            ev, _ = st.pay_signals(text)
            self.assertEqual(ev, [], text)


class Tiers(unittest.TestCase):
    def test_patient_only_payment(self):
        it = {"healthy_volunteers": False, "phases": [], "sponsor_class": "OTHER"}
        self.assertEqual(st.assign_tier(it, ["£20 voucher"], False), "paid_patients")

    def test_healthy_volunteer_payment(self):
        it = {"healthy_volunteers": True, "phases": [], "sponsor_class": "OTHER"}
        self.assertEqual(st.assign_tier(it, ["£20 voucher"], False), "paid")
        self.assertEqual(st.assign_tier(it, [], False), "healthy")
        it["phases"] = ["Phase I/II"]
        self.assertEqual(st.assign_tier(it, [], False), "likely_paid")

    def test_age_label(self):
        self.assertEqual(st.age_label("65 Years", None), "ages 65+")
        self.assertEqual(st.age_label("18 Years", "45 Years"), "ages 18–45")
        self.assertEqual(st.age_label("18 Years", None), "")
        self.assertEqual(st.age_label("2 Years", "17 Years"), "up to age 17")


class Topics(unittest.TestCase):
    def topics(self, core, summary=""):
        return st.match_topics(core, core + " " + summary)

    def test_matches(self):
        self.assertEqual(self.topics("Psilocybin for treatment-resistant depression"),
                         ["psilocybin"])
        self.assertIn("muscle", self.topics(
            "Whey protein and muscle protein synthesis after resistance exercise"))
        self.assertIn("ketamine", self.topics("Esketamine nasal spray in depression"))
        self.assertIn("migraine", self.topics("Chronic Migraine"))
        self.assertIn("cannabis", self.topics("x", "Participants take CBD oil daily"))
        self.assertIn("anxiety", self.topics("Generalised Anxiety Disorder"))

    def test_non_matches(self):
        self.assertEqual(self.topics("Left ventricular hypertrophy in hypertension"), [])
        self.assertEqual(self.topics("Hypertrophic cardiomyopathy registry"), [])
        self.assertEqual(self.topics("Enzyme therapy for Fabry disease",
                                     "a lysosomal storage disease (LSD)"), [])
        # anxiety is core-only: an outcome measure in the summary is not enough
        self.assertEqual(self.topics("Knee replacement", "HADS anxiety score at 6 weeks"), [])


def ctgov_study(**over):
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT01234567", "briefTitle": "A Phase 1 Study"},
            "statusModule": {
                "overallStatus": "RECRUITING",
                "studyFirstPostDateStruct": {"date": "2026-10-30"},
                "lastUpdatePostDateStruct": {"date": "2026-10-30"},
            },
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Pharma Ltd",
                                                           "class": "INDUSTRY"}},
            "descriptionModule": {"briefSummary": "Healthy volunteers receive drug X."},
            "conditionsModule": {"conditions": ["Healthy"]},
            "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE1"]},
            "eligibilityModule": {"healthyVolunteers": True, "sex": "ALL",
                                  "stdAges": ["ADULT"]},
            "contactsLocationsModule": {"locations": [
                {"facility": "Western General", "status": "RECRUITING", "city": "Edinburgh",
                 "country": "United Kingdom", "geoPoint": {"lat": 55.96, "lon": -3.23}},
                {"facility": "RVI", "status": "RECRUITING", "city": "Newcastle",
                 "country": "United Kingdom", "geoPoint": {"lat": 54.98, "lon": -1.62}},
            ]},
        }
    }
    study["protocolSection"].update(over)
    return study


class ClinicalTrialsGov(unittest.TestCase):
    def test_normalise_scottish_phase1(self):
        it = st.ctgov_normalise(ctgov_study())
        self.assertEqual([s["city"] for s in it["sites"]], ["Edinburgh"])
        self.assertEqual(it["tier"], "likely_paid")

    def test_drops_non_scottish(self):
        s = ctgov_study()
        s["protocolSection"]["contactsLocationsModule"]["locations"].pop(0)
        self.assertIsNone(st.ctgov_normalise(s))

    def test_drops_closed_scottish_site(self):
        s = ctgov_study()
        s["protocolSection"]["contactsLocationsModule"]["locations"][0]["status"] = "COMPLETED"
        self.assertIsNone(st.ctgov_normalise(s))

    def test_drops_children_only(self):
        s = ctgov_study(eligibilityModule={"healthyVolunteers": False, "stdAges": ["CHILD"]})
        self.assertIsNone(st.ctgov_normalise(s))


ISRCTN_XML = """<allTrials totalCount="1" xmlns="http://www.67bricks.com/isrctn"><fullTrial>
<trial lastUpdated="2026-10-30T16:04:48.258Z" version="1">
 <isrctn dateAssigned="2026-10-29T10:00:00Z">11453367</isrctn>
 <trialDescription>
  <title>Ketamine and memory in healthy adults</title>
  <scientificTitle>KETMEM</scientificTitle><acronym/>
  <plainEnglishSummary>Background and study aims
We want to see how ketamine affects memory.

Who can participate?
Healthy adults aged 18-45

What are the possible benefits and risks of participating?
Participants will receive £150 for their time.

Who is the main contact?
Dr A Person, a.person@ed.ac.uk</plainEnglishSummary>
 </trialDescription>
 <externalRefs><clinicalTrialsGovNumber>NCT01234567</clinicalTrialsGovNumber></externalRefs>
 <trialDesign><primaryStudyDesign>Interventional</primaryStudyDesign>
  <overallEndDate>2027-12-31T00:00:00.000Z</overallEndDate></trialDesign>
 <participants>
  <recruitmentCountries><country>Scotland</country></recruitmentCountries>
  <trialCentres><trialCentre><name>University of Edinburgh</name><city>Edinburgh</city>
   <state/><country>Scotland</country></trialCentre></trialCentres>
  <participantTypes><participantType>Healthy volunteer</participantType></participantTypes>
  <healthyVolunteersAllowed>true</healthyVolunteersAllowed>
  <ageRange>Adult</ageRange><gender>All</gender>
  <recruitmentStart>2026-11-01T00:00:00.000Z</recruitmentStart>
  <recruitmentEnd>2027-06-30T00:00:00.000Z</recruitmentEnd>
  <recruitmentStatusOverride/>
 </participants>
 <conditions><condition><description>Memory</description></condition></conditions>
 <interventions><intervention><description>Ketamine infusion</description></intervention>
 </interventions>
</trial></fullTrial></allTrials>"""


class Isrctn(unittest.TestCase):
    def trial(self):
        root = ET.fromstring(ISRCTN_XML)
        return next(st._iter_local(root, "trial"))

    def test_normalise(self):
        it = st.isrctn_normalise(self.trial(), TODAY)
        self.assertEqual(it["id"], "ISRCTN11453367")
        self.assertEqual(it["first_posted"], "2026-10-29")
        self.assertEqual(it["status"], "RECRUITING")
        self.assertIn("ketamine", it["topics"])
        self.assertEqual(it["tier"], "paid")
        self.assertEqual(it["contact"].get("email"), "a.person@ed.ac.uk")
        self.assertEqual(it["aliases"], ["NCT01234567"])

    def test_closed_by_date(self):
        self.assertIsNone(st.isrctn_normalise(self.trial(), dt.date(2027, 7, 1)))

    def test_merge_prefers_isrctn(self):
        a = st.isrctn_normalise(self.trial(), TODAY)
        b = st.ctgov_normalise(ctgov_study())
        merged = st.merge_duplicates([a, b])
        self.assertEqual([m["id"] for m in merged], ["ISRCTN11453367"])
        self.assertEqual(merged[0]["also"], b["url"])


class Digest(unittest.TestCase):
    def test_render_fits_and_numbers(self):
        a = st.isrctn_normalise(Isrctn().trial(), TODAY)
        b = st.ctgov_normalise(ctgov_study())
        b["id"], b["aliases"] = "NCT07654321", []
        items = [a, b]
        st.classify(items, TODAY - dt.timedelta(days=7))
        md = st.render_markdown(items, {"ISRCTN": {"ok": True, "count": 1, "error": None}},
                                TODAY - dt.timedelta(days=7), TODAY)
        self.assertIn("**1. Ketamine and memory in healthy adults**", md)
        self.assertIn("£150", md)
        self.assertLess(len(md), 4800)


if __name__ == "__main__":
    unittest.main()
