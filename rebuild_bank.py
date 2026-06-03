#!/usr/bin/env python3
"""Reconstruye banco_preguntas.json desde los checkpoints de página.
Corrige el mapeo de casos clínicos de forma integral."""

import json, re, time
from collections import defaultdict
from pathlib import Path

PROCESSED_DIR = Path(__file__).parent / "processed"
BANCO_PATH = Path(__file__).parent / "banco_preguntas.json"
REVIEW_PATH = Path(__file__).parent / "review_queue.json"

CASE_PREFIX_RE = re.compile(
    r'^(?:'
    r'CASO\s+(?:CL[ÍI]NICO|SERIADO)?\s*\d*\s*[:.]?\s*'
    r'|(?:Hombre|Mujer|Masculin[ao]|Femenin[ao]|Varón|Paciente)\s+(?:de\s+)?\d{2,3}\s*(?:años|año|meses|días)\b'
    r'|PACIENTE\s+(?:masculino|femenino|de\s+\d+)'
    r')',
    re.IGNORECASE,
)

DISEASE_HEADER_RE = re.compile(
    r'^(?:EDEMA\s+AGUDO\s+PULMONAR|TEP[.\s]|ESTADO\s+HIPEROSMOLAR|S[ÍI]NDROME\s+POLI[ÚU]RICO|'
    r'INSUFICIENCIA\s+SUPRARRENAL|ARTRITIS\s+S[ÉE]PTICA|NEFRITIS|HIPERTIROIDISMO|'
    r'SIADH\b|ACROMEGALIA|HIPERPARATIROIDISMO|LINFOMA|SMD\b|LMA\b|DENGUE|'
    r'OSTEOARTRITIS|ARTERITIS|PREECLAMPSIA|LUPUS\b|LES\b|POLICITEMIA\s+VERA|'
    r'N[ÓO]DULO\s+(?:PULMONAR|TIROIDEO)|EPOC\b|IAM\b|BIO[ÉE]TICA)',
    re.IGNORECASE,
)

HAS_AGE = re.compile(r'\b\d{2,3}\s*(?:años|año|meses|días)\b', re.IGNORECASE)
HAS_SYMPTOMS = re.compile(
    r'\b(?:fiebre|dolor|tos|disnea|edema|antecedente|acude|presenta|ingresa|'
    r'refiere|refiri[oó]|evoluci[oó]n|hospitalizad[ao])',
    re.IGNORECASE,
)
REF_PACIENTE = re.compile(r'\b(?:este|esta|dicho|dicha|del|al|la)\s+paciente\b', re.IGNORECASE)
NUMERADA = re.compile(r'^\d+[\.\)]')


def is_case_starter(text: str) -> bool:
    if CASE_PREFIX_RE.match(text):
        return True
    if DISEASE_HEADER_RE.match(text):
        return True
    return len(text) > 80 and bool(HAS_AGE.search(text)) and bool(HAS_SYMPTOMS.search(text))


def extract_case(text: str) -> tuple[str, str]:
    """Return (case_text, question_text) or (text, text) if can't split."""
    # Pattern: double newline separates case from question
    if '\n\n' in text:
        parts = text.split('\n\n')
        first = parts[0].strip()
        if is_case_starter(first):
            question = parts[-1].strip()
            case_text = '\n\n'.join(parts[:-1]).strip()
            if question and len(case_text) > 30:
                return case_text, question

    # Pattern: split at ¿ or ?
    m = re.search(r'[¿]', text) or re.search(r'\?\s*(?:\n|$)', text)
    if m and m.start() > 40:
        return text[:m.start()].strip(), text[m.start():].strip()

    # Pattern: first line is header, rest is question
    if '\n' in text:
        idx = text.find('\n')
        first, rest = text[:idx].strip(), text[idx:].strip()
        if re.match(r'^(CASO|EDEMA|TEP|NÓDULO|SÍNDROME)', first, re.IGNORECASE) and len(rest) > 10:
            return first + '\n' + rest.split('\n')[0] if '\n' in rest else first, rest
        if NUMERADA.match(first) and is_case_starter(rest):
            return rest, first

    # Pattern: numbered question followed by case text
    if NUMERADA.match(text.split('\n')[0]) and '\n' in text:
        first, rest = text.split('\n', 1)
        if is_case_starter(rest.strip()):
            return rest.strip(), first.strip()

    # Pattern: text ends with a recognizable question starter
    starters = [
        r'A partir de qué', r'C[uú]al\s+(es|de|sería|considera)',
        r'El diagnóstico', r'El tratamiento', r'C[oó]mo\s+(clasificaría|se)',
        r'Qué\s+(dato|estudio|factor|intervención|medida|índice)',
        r'De acuerdo', r'Seg[uú]n', r'En qué',
    ]
    for p in starters:
        m = re.search(r'(\.\s*|\n)(' + p + r'.*?)$', text, re.IGNORECASE)
        if m:
            case_text = text[:m.start(1)].strip()
            question = m.group(2).strip()
            if len(case_text) > 40 and len(question) > 10:
                return case_text, question

    return text, text


def main():
    print("=== Reconstrucción integral del banco de preguntas ===\n")

    # ─── Load all checkpoints ───
    all_qs = []
    for ckpt in sorted(PROCESSED_DIR.glob("*.json")):
        data = json.loads(ckpt.read_text())
        for i, p in enumerate(data.get("preguntas", [])):
            p.setdefault("caso_clinico", "")
            p.setdefault("fuente", data.get("filename", ""))
            p.setdefault("pagina", data.get("pagina", 0))
            p["_original_idx"] = i
            p.setdefault("justificacion", "")
            p.setdefault("confidence", 0.0)
            p.setdefault("incomplete", False)
            p.setdefault("verified", False)
            p.setdefault("tipo", "multiple_choice")
            # Normalize opciones
            if not p.get("opciones") or not any(p["opciones"].values()):
                p["opciones"] = {"V": "Verdadero", "F": "Falso"}
                p["tipo"] = "true_false"
            else:
                p["opciones"] = {k: (v or "") for k, v in p["opciones"].items()}
            all_qs.append(p)

    print(f"Total preguntas crudas: {len(all_qs)}\n")

    # ─── Process per source file ───
    by_file = defaultdict(list)
    for p in all_qs:
        by_file[p["fuente"]].append(p)

    final_preguntas = []
    stats_fixed, stats_inherited, stats_gender = 0, 0, 0

    for fname in sorted(by_file.keys()):
        qs = by_file[fname]
        qs.sort(key=lambda p: (p["pagina"], p.get("_original_idx", 0)))

        active_case = None
        active_case_page = -1

        for p in qs:
            pregunta = p["pregunta"]
            pagina = p["pagina"]
            cc = (p.get("caso_clinico", "") or "").strip()

            # --- Phase 1: Extract case from embedded text ---
            if (not cc or cc == pregunta) and is_case_starter(pregunta):
                case_text, cleaned_q = extract_case(pregunta)
                if case_text != cleaned_q:
                    p["caso_clinico"] = case_text
                    p["pregunta"] = cleaned_q
                    cc = case_text
                    stats_fixed += 1
                elif case_text and cc != case_text:
                    p["caso_clinico"] = case_text
                    cc = case_text
                    stats_fixed += 1

            # --- Phase 2: Inherit active case ---
            if cc and cc != p["pregunta"] and len(cc) > 20:
                active_case = cc
                active_case_page = pagina
                continue

            if active_case and not cc:
                dist = pagina - active_case_page
                if 0 <= dist <= 1:
                    p["caso_clinico"] = active_case
                    stats_inherited += 1
                elif REF_PACIENTE.search(pregunta) and dist <= 8:
                    p["caso_clinico"] = active_case
                    stats_inherited += 1

        # Collect processed questions for this file
        for p in qs:
            # --- Phase 3: Gender mismatch fix ---
            cc2 = (p.get("caso_clinico", "") or "").strip()
            pregunta2 = p["pregunta"]
            if cc2 and cc2 != pregunta2:
                male = bool(re.search(
                    r'\b(Hombre|Varón|Masculin[ao])\b', cc2, re.IGNORECASE,
                ))
                female_ref = bool(re.search(
                    r'\b(la paciente|dicha paciente|ella)\b', pregunta2, re.IGNORECASE,
                ))
                if male and female_ref:
                    p["caso_clinico"] = ""
                    stats_gender += 1

            final_preguntas.append(p)

    # ─── Dedup (full question text, keep best version) ───
    seen = {}
    for p in final_preguntas:
        key = (p["fuente"], p["pagina"], p["pregunta"].strip())
        if key not in seen:
            seen[key] = p
        else:
            existing = seen[key]
            # Prefer the one with caso_clinico
            if not existing.get("caso_clinico") and p.get("caso_clinico"):
                seen[key] = p
            # Prefer higher confidence
            elif p.get("confidence", 0) > existing.get("confidence", 0):
                seen[key] = p

    final_preguntas = list(seen.values())

    # Mark single-option questions before separation
    for p in final_preguntas:
        if len(p.get("opciones", {})) <= 1:
            p["verified"] = False
            p["incomplete"] = True
            p["confidence"] = 0.1

    # ─── Separate verified / unverified ───
    verified = [p for p in final_preguntas if p.get("verified") and p.get("confidence", 0) >= 0.5]
    unverified = [p for p in final_preguntas if not (p.get("verified") and p.get("confidence", 0) >= 0.5)]

    # ─── Build output ───
    banco = {
        "meta": {
            "total_preguntas": len(verified),
            "pendientes_revision": len(unverified),
            "total_archivos": len(by_file),
            "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "preguntas": [p for p in verified],
    }

    # Sort by (fuente, pagina, pregunta)
    banco["preguntas"].sort(key=lambda p: (p["fuente"], p["pagina"], p.get("_original_idx", 0)))
    for p in banco["preguntas"]:
        p.pop("_original_idx", None)

    BANCO_PATH.write_text(json.dumps(banco, indent=2, ensure_ascii=False))

    review = {
        "meta": {"generado": time.strftime("%Y-%m-%dT%H:%M:%S")},
        "preguntas": [p for p in unverified],
    }
    for p in review["preguntas"]:
        p.pop("_original_idx", None)
    REVIEW_PATH.write_text(json.dumps(review, indent=2, ensure_ascii=False))

    # ─── Stats ───
    with_cc = sum(1 for p in banco["preguntas"] if p.get("caso_clinico","") and p["caso_clinico"] != p.get("pregunta",""))
    no_cc = sum(1 for p in banco["preguntas"] if not p.get("caso_clinico","").strip())
    same_cc = sum(1 for p in banco["preguntas"] if p.get("caso_clinico","").strip() == p.get("pregunta","").strip())

    print(f"Reporte:")
    print(f"  Casos extraídos de preguntas embebidas: {stats_fixed}")
    print(f"  Casos heredados entre páginas: {stats_inherited}")
    print(f"  Gender mismatches corregidos: {stats_gender}")
    print(f"  Deduplicadas: {len(final_preguntas)} → {len(verified)}")
    print(f"\nBanco final:")
    print(f"  Total: {len(verified)} preguntas")
    print(f"  Con caso_clínico: {with_cc}")
    print(f"  Sin caso_clínico: {no_cc}")
    print(f"  Pendientes revisión: {len(unverified)}")


if __name__ == "__main__":
    main()
