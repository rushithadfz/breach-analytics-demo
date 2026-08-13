"""Scores the resolved Person/ExposureFlag state against the corpus
generator's manifest.json ground truth (brief section 4, 'Accuracy
measurement'). This module is only ever run in evaluation — the system
itself has no access to the manifest at inference time.

Matching is done by planted VALUE, not by document proximity: each
manifest plant records the exact string that was embedded (an SSN, a card
number, a chosen name variant, ...), and our extractions store the same
raw/normalized value plus which resolved Person they were linked to. This
sidesteps the ambiguity a document-overlap approach would hit on
multi-person spreadsheets, where dozens of unrelated people share one
relpath.
"""
from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EntityLink, Extraction, ExposureFlag, Person

_FIELD_TO_CATEGORY = {
    "ssn": "ssn", "ssn_partial": "ssn",
    "dob": "dob",
    "drivers_license": "drivers_license",
    "passport": "passport",
    "bank_account": "financial_account", "financial_account_partial": "financial_account",
    "card_number": "card_number", "card_number_partial": "card_number",
    "medical": "medical",
    "login_credentials": "login_credentials",
    "home_address": "home_address",
    "phone": "phone",
    "email": "email",
    "full_name_variant": "full_name",
}


def _load_manifest(manifest_path: str) -> dict:
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def score_against_manifest(db: Session, manifest_path: str) -> dict:
    manifest = _load_manifest(manifest_path)
    all_gt_people = {p["person_id"] for p in manifest["people"]}

    # (category, value) -> set of gt person_ids that planted exactly that value.
    gt_value_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    gt_person_categories: dict[str, set[str]] = defaultdict(set)
    gt_people_with_evidence: set[str] = set()

    for d in manifest["documents"]:
        for plant in d["plants"]:
            if plant["is_false_positive_trap"] or plant["person_id"] == "NONE":
                continue
            category = _FIELD_TO_CATEGORY.get(plant["field"])
            if not category:
                continue
            gt_value_index[(category, plant["value"])].add(plant["person_id"])
            gt_person_categories[plant["person_id"]].add(category)
            gt_people_with_evidence.add(plant["person_id"])

    # (category, normalized_value) -> set of predicted person_ids that were linked to it.
    pred_value_index: dict[tuple[str, str], set[int]] = defaultdict(set)
    rows = db.execute(
        select(EntityLink, Extraction).join(Extraction, EntityLink.extraction_id == Extraction.id)
    ).all()
    for link, extraction in rows:
        pred_value_index[(extraction.category.value, extraction.normalized_value)].add(link.person_id)
        pred_value_index[(extraction.category.value, extraction.raw_value)].add(link.person_id)

    # For each GT person, gather every predicted person any of their planted
    # values landed on; the plurality winner is "the system's answer" for
    # that identity.
    gt_to_predicted_votes: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for (category, value), gt_pids in gt_value_index.items():
        predicted_pids = pred_value_index.get((category, value))
        if not predicted_pids:
            continue
        for gt_pid in gt_pids:
            for pred_pid in predicted_pids:
                gt_to_predicted_votes[gt_pid][pred_pid] += 1

    true_positive, missed = 0, 0
    matched_pred_person_for_gt: dict[str, int] = {}
    for gt_pid in gt_people_with_evidence:
        votes = gt_to_predicted_votes.get(gt_pid)
        if not votes:
            missed += 1
            continue
        best_pred = max(votes.items(), key=lambda kv: kv[1])[0]
        true_positive += 1
        matched_pred_person_for_gt[gt_pid] = best_pred

    # Merge errors: one predicted person is "the answer" for >1 distinct GT person.
    pred_to_gt: dict[int, set[str]] = defaultdict(set)
    for gt_pid, pred_pid in matched_pred_person_for_gt.items():
        pred_to_gt[pred_pid].add(gt_pid)
    merge_errors = sum(1 for gts in pred_to_gt.values() if len(gts) > 1)
    people_lost_to_merges = sum(len(gts) - 1 for gts in pred_to_gt.values() if len(gts) > 1)

    total_gt = len(all_gt_people)
    total_pred_persons = db.execute(select(Person)).scalars().all()
    person_recall = true_positive / total_gt if total_gt else 0.0
    # A merged predicted person only counts as "correct" for one of the
    # people it absorbed — the rest are effectively false negatives even
    # though the value-matching step counted them as a vote-based match.
    effective_true_positive = true_positive - people_lost_to_merges
    person_precision = effective_true_positive / len(total_pred_persons) if total_pred_persons else 0.0

    flags_by_person: dict[int, set[str]] = defaultdict(set)
    for flag in db.execute(select(ExposureFlag)).scalars().all():
        flags_by_person[flag.person_id].add(flag.category.value)

    # full_name is the identity key used to resolve people, not an exposure
    # category in its own right (see app/services/exposure_table.py
    # FLAG_CATEGORIES) — scoring it here would always read as 0% recall
    # since no ExposureFlag row is ever created for it.
    category_stats = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0})
    for gt_pid, pred_pid in matched_pred_person_for_gt.items():
        expected = gt_person_categories.get(gt_pid, set()) - {"full_name"}
        predicted = flags_by_person.get(pred_pid, set())
        for cat in expected:
            if cat in predicted:
                category_stats[cat]["tp"] += 1
            else:
                category_stats[cat]["fn"] += 1
        for cat in predicted - expected:
            category_stats[cat]["fp"] += 1

    per_category_accuracy = {}
    for cat, s in category_stats.items():
        denom = s["tp"] + s["fn"]
        per_category_accuracy[cat] = {
            **s,
            "recall": round(s["tp"] / denom, 3) if denom else None,
            "precision": round(s["tp"] / (s["tp"] + s["fp"]), 3) if (s["tp"] + s["fp"]) else None,
        }

    return {
        "person_level": {
            "ground_truth_people_with_evidence": len(gt_people_with_evidence),
            "ground_truth_people_total": total_gt,
            "predicted_persons": len(total_pred_persons),
            "true_positive_matches": true_positive,
            "missed_entirely": missed,
            "merge_errors": merge_errors,
            "people_lost_to_merges": people_lost_to_merges,
            "recall": round(person_recall, 3),
            "precision": round(person_precision, 3),
        },
        "per_category_flag_accuracy": per_category_accuracy,
        "value_level_precision": _score_value_precision(db, manifest),
        "page_attribution": _score_page_attribution(db, manifest),
        "third_party_contamination": _score_third_party(db, manifest),
        "recall_by_context_style": _recall_by_style(manifest, gt_value_index, pred_value_index),
    }


def _score_value_precision(db: Session, manifest: dict) -> dict:
    """Is each extracted VALUE something the corpus actually contains?

    The per-category figures above answer a different and much easier
    question: does this person carry a flag for this category. A person
    with one real phone and three fabricated ones scores as a clean true
    positive there, which is exactly how 156 phone numbers invented out
    of card digits sat at "precision 1.000" for the whole project.

    This is the number that matters for a notification list. Telling
    someone their SSN was exposed and citing a value that was never in
    the document is worse than missing them: it is a false statement in
    a legal notice.
    """
    # Compare the way the pipeline canonicalises, not by raw string.
    #
    # A phone written "001-248-518-1398" is correctly extracted as
    # "248-518-1398" and normalised to "2485181398"; comparing raw
    # strings marked that a fabrication. 126 of the phone values flagged
    # as uncorroborated were correct extractions being mis-scored, which
    # would have sent me off fixing a detector that was already right.
    # The scorer has to speak the same dialect as the extractor.
    from app.pipeline.detectors.deterministic import normalize_phone

    def variants(category: str, value: str) -> set[str]:
        v = str(value).strip()
        out = {v}
        if category == "phone":
            out.add(normalize_phone(v))
        return {x for x in out if x}

    planted: dict[str, set[str]] = defaultdict(set)
    for d in manifest["documents"]:
        for pl in d["plants"]:
            category = _FIELD_TO_CATEGORY.get(pl["field"])
            if category:
                planted[category] |= variants(category, pl["value"])

    rows = db.execute(
        select(Extraction).join(EntityLink, EntityLink.extraction_id == Extraction.id)
    ).scalars().all()

    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"linked": 0, "corroborated": 0})
    samples: dict[str, list[str]] = defaultdict(list)
    for e in rows:
        cat = e.category.value
        s = stats[cat]
        s["linked"] += 1
        known = planted.get(cat, set())
        seen = variants(cat, e.raw_value) | variants(cat, e.normalized_value)
        if seen & known:
            s["corroborated"] += 1
        elif len(samples[cat]) < 3:
            samples[cat].append(e.normalized_value[:60])

    out = {}
    for cat, s in sorted(stats.items()):
        out[cat] = {
            **s,
            "uncorroborated": s["linked"] - s["corroborated"],
            "precision": round(s["corroborated"] / s["linked"], 3) if s["linked"] else None,
            "examples_not_in_corpus": samples[cat],
        }
    linked = sum(s["linked"] for s in stats.values())
    corrob = sum(s["corroborated"] for s in stats.values())
    out["_overall"] = {
        "linked": linked, "corroborated": corrob,
        "precision": round(corrob / linked, 3) if linked else None,
    }
    return out


def _score_page_attribution(db: Session, manifest: dict) -> dict:
    """Of the values we both found AND can cite a page for, how often is
    the cited page the page the value was actually planted on?

    Only meaningful on a corpus with multi-page documents. On the
    previous corpus every document was one page, so this would have
    scored 100% while proving nothing.
    """
    truth: dict[tuple[str, str], int] = {}
    for d in manifest["documents"]:
        rel = d["relpath"]
        for plant in d["plants"]:
            if plant["is_false_positive_trap"] or plant["person_id"] == "NONE":
                continue
            if plant.get("page"):
                truth[(rel, plant["value"])] = plant["page"]

    from app.db.models import Document

    rows = db.execute(
        select(Extraction, Document).join(Document, Extraction.document_id == Document.id)
    ).all()

    correct = wrong = uncited = 0
    off_by = defaultdict(int)
    for ext, doc in rows:
        rel = doc.relpath.replace("\\", "/")
        want = truth.get((rel, ext.raw_value)) or truth.get((rel, ext.normalized_value))
        if want is None:
            continue
        if ext.page_number is None:
            uncited += 1
        elif ext.page_number == want:
            correct += 1
        else:
            wrong += 1
            off_by[ext.page_number - want] += 1

    graded = correct + wrong
    return {
        "values_with_known_page": graded + uncited,
        "cited_correct_page": correct,
        "cited_wrong_page": wrong,
        "found_but_no_page_cited": uncited,
        "page_accuracy": round(correct / graded, 3) if graded else None,
        "offset_histogram": dict(sorted(off_by.items())),
    }


def _score_third_party(db: Session, manifest: dict) -> dict:
    """Staff details planted in a document belong to the case handler,
    not the data subject. Attributing one to the subject is a precision
    failure the previous corpus could not detect, because every name in
    every document belonged to the subject."""
    third_party_values: set[tuple[str, str]] = set()
    for d in manifest["documents"]:
        for plant in d["plants"]:
            if not plant.get("is_third_party"):
                continue
            category = _FIELD_TO_CATEGORY.get(plant["field"])
            if category:
                third_party_values.add((category, plant["value"]))

    rows = db.execute(
        select(EntityLink, Extraction).join(Extraction, EntityLink.extraction_id == Extraction.id)
    ).all()

    contaminated = 0
    by_category = defaultdict(int)
    for _link, ext in rows:
        for value in (ext.raw_value, ext.normalized_value):
            if (ext.category.value, value) in third_party_values:
                contaminated += 1
                by_category[ext.category.value] += 1
                break

    return {
        "third_party_values_planted": len(third_party_values),
        "third_party_values_attributed_to_a_subject": contaminated,
        "by_category": dict(by_category),
    }


def _recall_by_style(manifest: dict, gt_value_index, pred_value_index) -> dict:
    """Recall split by how the value was presented — a labelled form
    field, a sentence, a photographed ID, a screenshot. This is the
    number that says whether the pipeline actually reads documents or
    just matches labels."""
    style_stats = defaultdict(lambda: {"found": 0, "total": 0})
    for d in manifest["documents"]:
        for plant in d["plants"]:
            if plant["is_false_positive_trap"] or plant["person_id"] == "NONE":
                continue
            category = _FIELD_TO_CATEGORY.get(plant["field"])
            if not category:
                continue
            style = plant.get("context_style") or "unlabelled"
            style_stats[style]["total"] += 1
            if pred_value_index.get((category, plant["value"])):
                style_stats[style]["found"] += 1

    return {
        style: {**s, "recall": round(s["found"] / s["total"], 3) if s["total"] else None}
        for style, s in sorted(style_stats.items())
    }
