#!/usr/bin/env python3
"""Corrige el mapeo de casos clínicos en banco_preguntas.json.

Fases:
  1. Re-extraer caso_clinico de preguntas que lo tienen embebido
  2. Heredar casos entre páginas del mismo archivo fuente
  3. Corregir gender mismatches
  4. Detectar y marcar preguntas con 1 opción
  5. Reportar casos sueltos
"""

import json
import re
from collections import defaultdict
from pathlib import Path

BANCO_PATH = Path(__file__).parent / "banco_preguntas.json"
PROCESSED_DIR = Path(__file__).parent / "processed"

# ─── Patrones de inicio de caso clínico ───

CASE_PREFIX_RE = re.compile(
    r'^(?:'
    r'CASO\s+(?:CLÍNICO|CLINICO)?\s*\d*\s*[:.]?\s*'
    r'|CASO SERIADO\s+\d+\s*[:.]?\s*'
    r'|CASO\s+\d+\s*[:.]?\s*'
    r'|(?:Hombre|Mujer|Masculin[ao]|Femenin[ao]|Varón|Paciente)\s+(?:de\s+)?\d+\s*(?:años|año|meses|días)\b'
    r'|PACIENTE\s+(?:masculino|femenino|de\s+\d+)'
    r')',
    re.IGNORECASE,
)

DISEASE_HEADER_RE = re.compile(
    r'^(?:'
    r'NEUMONÍA|TEP|TROMBOEMBOLISMO|PANCREATITIS|MENINGITIS|'
    r'APENDICITIS|CETOACIDOSIS|POLICITEMIA|ARTROSIS|ART(?:RITIS)?|'
    r'HEPATITIS|ENFERMEDAD\s+RENAL|NEFROPATÍA|NÓDULO\s+PULMONAR|'
    r'NÓDULO\s+TIROIDEO|EPOC|DM\b|INFARTO|IAM\b|DIABETES|'
    r'EDEMA\s+AGUDO\s+PULMONAR|ESTADO\s+HIPEROSMOLAR|'
    r'SÍNDROME\s+POLIURICO|SINDROME\s+POLIURICO|'
    r'INSUFICIENCIA\s+SUPRARRENAL|ARTRITIS\s+SÉPTICA|ARTRITIS\s+SEPTICA|'
    r'GLOMERULONEFRITIS|NEFRITIS|HIPERTIROIDISMO|'
    r'SIADH\b|ACROMEGALIA|HIPERPARATIROIDISMO|'
    r'LINFOMA\s+NO\s+HODGKIN|SMD\b|LMA\b|DENGUE|'
    r'OSTEOARTRITIS|ARTERITIS|PREECLAMPSIA|LUPUS\b|LES\b|'
    r'BIOÉTICA|BIOETICA|'
    r'POLICITEMIA\s+VERA'
    r')\s',
    re.IGNORECASE,
)

HAS_AGE = re.compile(r'\b\d{2,3}\s*(?:años|año|meses|días)\b', re.IGNORECASE)
HAS_SYMPTOMS = re.compile(
    r'\b(?:fiebre|dolor|tos|disnea|edema|antecedente|'
    r'padecimiento|acude|presenta|ingresa|hospitalizad[ao]|'
    r'refiere|refirio|refirió|curso|evolución|evolucion)',
    re.IGNORECASE,
)

# Pregunta referenciando "este paciente", "la paciente", etc. (solo para heredar)
REF_PACIENTE_RE = re.compile(
    r'\b(?:este|esta|dicho|dicha|del|al)\s+paciente\b',
    re.IGNORECASE,
)


def is_case_starter(text: str) -> bool:
    if CASE_PREFIX_RE.match(text):
        return True
    if DISEASE_HEADER_RE.match(text):
        return True
    return len(text) > 100 and bool(HAS_AGE.search(text)) and bool(HAS_SYMPTOMS.search(text))


def split_case_from_question(text: str) -> tuple[str, str]:
    """Try to split caso_clinico from pregunta if they're fused.

    Handles patterns:
    - "DISEASE\nPatient desc...\n\nQuestion?" → case = everything up to the actual question
    - "Caso seriado X\nPatient desc... Question?" → same
    - "1. Question?\nPatient desc..." → case = patient desc (after numbered question)
    - "Patient desc... El diagnóstico es:" → case = everything up to "El diagnóstico"
    """
    lines = text.split('\n')
    
    # If text has doubled newline, split around it (common pattern: case \n\n question)
    if '\n\n' in text:
        parts = text.split('\n\n')
        # First part is typically header + case description
        # Last part is typically the question
        # Check if first part looks like a case starter
        first = parts[0].strip()
        if is_case_starter(first):
            # Everything before the last paragraph could be the case
            case_parts = parts[:-1]
            question = parts[-1].strip()
            case_text = '\n\n'.join(case_parts).strip()
            if question and len(case_text) > 40:
                return case_text, question
    
    # Try splitting at ¿ or ? 
    m = re.search(r'[¿]', text)
    if not m:
        m = re.search(r'\?\s*(?:\n|$)', text)
    if m:
        idx = m.start()
        before = text[:idx].strip()
        after = text[idx:].strip()
        if len(before) > 60:
            return before, after
    
    # For texts ending with : (colon), the last sentence is the question
    if re.search(r':\s*$', text):
        # Find the last sentence before colon - check if there's a recognizable question
        sentences = re.split(r'(?<=[.!])\s+', text)
        if len(sentences) >= 2:
            # Try: last sentence is the question
            question = sentences[-1]
            case_text = ' '.join(sentences[:-1])
            if len(case_text) > 40 and len(question) > 5:
                return case_text, question
    
    # If first line starts with numbered item and next lines are case text
    if '\n' in text:
        idx = text.find('\n')
        first_line = text[:idx].strip()
        rest = text[idx:].strip()
        numbered_match = re.match(r'^\d+[\.\)]\s+', first_line)
        if numbered_match and len(rest) > 40:
            if is_case_starter(rest) or (HAS_AGE.search(rest) and HAS_SYMPTOMS.search(rest)):
                return rest, first_line
        # CASO X ... \n Question pattern (DERIVAIMSS-style)
        caso_match = re.match(
            r'^(CASO\s+(?:CL[ÍI]NICO\s+)?\d+|CASO SERIADO\s+\d+)\b(.+)$',
            first_line, re.IGNORECASE,
        )
        if caso_match and len(rest) > 10:
            # First line is case header + maybe some case text, rest is the question
            full_case = first_line + '\n' + rest.split('\n')[0] if '\n' in rest else first_line
            # Actually the case is the first line + possibly the rest until it looks like a question
            # Rest likely starts with a question or numbered item
            if re.match(r'^[\d¿]', rest.lstrip()):
                return first_line + '\n' + rest.split('\n')[0] if '\n' in rest else first_line, rest
            return first_line, rest
    
    # Second-to-last resort: find last sentence with question pattern
    for sep in [r'\n(?=\d+[\.\)])', r'\n(?=[¿])']:
        parts = re.split(sep, text)
        if len(parts) >= 2:
            case_text = parts[0].strip()
            question = '\n'.join(parts[1:]).strip()
            if len(case_text) > 40 and len(question) > 10:
                return case_text, question
    
    # Last resort: find a sentence that starts with a question-like phrase at the end
    # Common question starters in medical exams
    question_starters = [
        r'A partir de qué', r'A partir de que',
        r'C[uú]al\s+(es|de|sería|considera)',
        r'El diagnóstico', r'El tratamiento',
        r'C[oó]mo\s+(clasificaría|se)',
        r'Qué\s+(dato|estudio|factor|intervención|medida)',
        r'En qué', r'En cuánto',
        r'Cuál\s+(es|de|sería)',
        r'De acuerdo', r'Para qué',
        r'Seg[uú]n', r'Qué\s+índice',
    ]
    for pattern in question_starters:
        m = re.search(r'(\.\s*|\n)(' + pattern + r'.*?)$', text, re.IGNORECASE)
        if m:
            case_text = text[:m.start(1)].strip()
            question = m.group(2).strip()
            if len(case_text) > 40 and len(question) > 10:
                return case_text, question
    
    # Very last resort for texts ending with question sentence
    # Try to split at last period that's followed by a question-like phrase
    m = re.search(r'(\.\s+)((?:El|La|Los|Las)\s+(?:diagnóstico|tratamiento|conducta|siguiente)\b.*?)$', text, re.IGNORECASE)
    if m:
        case_text = text[:m.start(1)].strip()
        question = m.group(2).strip()
        if len(case_text) > 40 and len(question) > 10:
            return case_text, question
    
    return text, text


def fix_embedded_case(p):
    """Fix question where case text is embedded in pregunta field."""
    pregunta = p.get('pregunta', '')
    existing_cc = p.get('caso_clinico', '') or ''
    
    # Skip if already has a valid case
    if existing_cc and existing_cc != pregunta and len(existing_cc) > 20:
        return False
    
    # Detect if pregunta has embedded case text
    if is_case_starter(pregunta):
        case_text, cleaned_q = split_case_from_question(pregunta)
        if case_text and case_text != cleaned_q:
            p['caso_clinico'] = case_text
            p['pregunta'] = cleaned_q
            return True
    
    return False


def fix_gender_mismatch(p):
    """Remove caso_clinico if gender doesn't match the question."""
    cc = p.get('caso_clinico', '') or ''
    pregunta = p.get('pregunta', '')
    if not cc or cc == pregunta:
        return False
    
    male_in_cc = bool(re.search(
        r'\b(Hombre\b|Varón\b|Masculin[ao]\b|Paciente\s+(?:masculino|de\s+\d+))',
        cc, re.IGNORECASE,
    ))
    female_ref_in_q = bool(re.search(
        r'\b(la paciente|dicha paciente|ella\b)',
        pregunta, re.IGNORECASE,
    ))
    
    if male_in_cc and female_ref_in_q:
        print(f"  GENDER MISMATCH: quitando caso_clinico (Hombre en caso, 'la paciente' en pregunta)")
        print(f"    pregunta: {pregunta[:80]}...")
        print(f"    caso: {cc[:80]}...")
        p['caso_clinico'] = ''
        return True
    
    return False


def main():
    print("=== Corrección de mapeo de casos clínicos ===\n")
    
    with open(BANCO_PATH) as f:
        banco = json.load(f)
    
    preguntas = banco['preguntas']
    stats = {'total': len(preguntas), 'fixed_embedded': 0, 'fixed_gender': 0,
             'single_option': 0, 'single_case': 0, 'no_case': 0}
    
    # ─── Phase 0: Basic stats ───
    print("--- Estadísticas iniciales ---")
    with_cc = sum(1 for p in preguntas if p.get('caso_clinico','') and p['caso_clinico'].strip() != p['pregunta'].strip())
    no_cc = sum(1 for p in preguntas if not p.get('caso_clinico','').strip())
    same_cc = sum(1 for p in preguntas if p.get('caso_clinico','').strip() == p['pregunta'].strip())
    print(f"  Con caso_clinico != pregunta: {with_cc}")
    print(f"  Sin caso_clinico: {no_cc}")
    print(f"  caso_clinico == pregunta: {same_cc}\n")
    
    # ─── Phase 1: Fix embedded cases ───
    print("--- Fase 1: Re-extraer caso_clinico de preguntas embebidas ---")
    for p in preguntas:
        if fix_embedded_case(p):
            stats['fixed_embedded'] += 1
    print(f"  Corregidas: {stats['fixed_embedded']}\n")
    
    # ─── Phase 1b: Inherit cases across pages per source file ───
    print("--- Fase 1b: Heredar casos entre páginas del mismo archivo ---")
    by_file = defaultdict(list)
    for i, p in enumerate(preguntas):
        by_file[p['fuente']].append((i, p))
    
    total_inherited = 0
    for fname in sorted(by_file.keys()):
        qs = by_file[fname]
        qs.sort(key=lambda x: (x[1]['pagina'], x[0]))
        
        active_case = None
        active_case_page = -1
        
        for idx, p in qs:
            pregunta = p.get('pregunta', '')
            pagina = p.get('pagina', 0)
            cc = p.get('caso_clinico', '') or ''
            
            # If question already has a valid case, make it the active one
            if cc and cc != pregunta and len(cc) > 20:
                if 'Pregunta anterior no visible' not in cc:
                    active_case = cc
                    active_case_page = pagina
                    continue
            
            # Try to detect case starter now
            if is_case_starter(pregunta):
                case_text, cleaned_q = split_case_from_question(pregunta)
                if case_text and case_text != cleaned_q:
                    p['caso_clinico'] = case_text
                    p['pregunta'] = cleaned_q
                    active_case = case_text
                    active_case_page = pagina
                    stats['fixed_embedded'] += 1
                    continue
                elif case_text and not cc:
                    p['caso_clinico'] = case_text
                    active_case = case_text
                    active_case_page = pagina
                    stats['fixed_embedded'] += 1
                    continue
            
            # Inherit active case
            if not active_case or cc:
                continue
            
            # If on same page or within 1 page gap, try to inherit
            page_dist = pagina - active_case_page
            if page_dist < 0:
                continue
            
            # Questions referencing "paciente" can inherit within 8 pages
            if REF_PACIENTE_RE.search(pregunta) and page_dist <= 8:
                p['caso_clinico'] = active_case
                total_inherited += 1
            # Numbered questions (like "3. ...", "4. ...") on same page inherit
            elif page_dist == 0:
                p['caso_clinico'] = active_case
                total_inherited += 1
            # New page continuation: check if it looks like a follow-up
            elif page_dist == 1 and len(pregunta) < 100 and not is_case_starter(pregunta):
                # Short question on next page could be continuation
                p['caso_clinico'] = active_case
                total_inherited += 1
    
    print(f"  Heredadas entre páginas: {total_inherited}\n")
    
    # ─── Phase 2: Gender mismatches ───
    print("--- Fase 2: Corregir gender mismatches ---")
    for p in preguntas:
        if fix_gender_mismatch(p):
            stats['fixed_gender'] += 1
    print(f"  Gender mismatches corregidos: {stats['fixed_gender']}\n")
    
    # ─── Phase 3: Detectar preguntas con 1 opción ───
    print("--- Fase 3: Preguntas con 1 opción ---")
    single_option = []
    for i, p in enumerate(preguntas):
        opts = p.get('opciones', {})
        if len(opts) <= 1:
            single_option.append((i, p['fuente'], p['pagina'], p['pregunta'][:80]))
            stats['single_option'] += 1
    
    print(f"  Preguntas con ≤1 opción: {stats['single_option']}")
    for idx, fuente, pag, q in single_option:
        print(f"    [{idx}] {fuente} p{pag}: {q}...")
    
    # Reprocess DERIVAIMSS pages that have single-option questions
    # These need the LLM to re-extract, but mark them as incomplete for now
    for i, p in enumerate(preguntas):
        opts = p.get('opciones', {})
        if len(opts) <= 1 and p['fuente'] == 'DERIVAIMSS FINAL ;3.pdf':
            # Mark for reprocessing
            p['confidence'] = min(p.get('confidence', 0), 0.1)
            p['verified'] = False
            p['incomplete'] = True
    print(f"  Marcadas como incomplete/verified=False para reprocesar\n")
    
    # ─── Final stats ───
    print("--- Estadísticas finales ---")
    with_cc = sum(1 for p in preguntas if p.get('caso_clinico','') and p['caso_clinico'].strip() != p['pregunta'].strip())
    no_cc = sum(1 for p in preguntas if not p.get('caso_clinico','').strip())
    same_cc = sum(1 for p in preguntas if p.get('caso_clinico','').strip() == p['pregunta'].strip())
    print(f"  Con caso_clinico != pregunta: {with_cc}")
    print(f"  Sin caso_clinico: {no_cc}")
    print(f"  caso_clinico == pregunta: {same_cc}")
    
    # Single-case stats
    case_counts = defaultdict(int)
    for p in preguntas:
        cc = p.get('caso_clinico', '').strip()
        if cc and cc != p.get('pregunta', '').strip():
            case_counts[cc] += 1
    single_case_qs = [(cc, cnt) for cc, cnt in case_counts.items() if cnt == 1]
    print(f"  Casos con 1 sola pregunta: {len(single_case_qs)}")
    
    # ─── Save ───
    banco['meta']['generado'] = __import__('time').strftime('%Y-%m-%dT%H:%M:%S')
    banco_path = Path(BANCO_PATH)
    backup = banco_path.with_suffix('.json.bak')
    if not backup.exists():
        banco_path.rename(backup)
        print(f"\n  Backup → {backup.name}")
    
    banco_path.write_text(json.dumps(banco, indent=2, ensure_ascii=False))
    print(f"  Banco actualizado → {banco_path.name}")
    
    print("\n=== Corrección completada ===")


if __name__ == '__main__':
    main()
