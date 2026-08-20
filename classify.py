"""
Heuristic classifier: decide whether a restricted-party-list name string is an
organization/company (keep) or an individual person (drop), for rows where the
source data doesn't reliably tag "type". Used only as a fallback when a row's
official type field is blank - rows explicitly tagged Entity/Individual/Vessel/
Aircraft are trusted as-is and never run through this heuristic.

This is intentionally conservative: false negatives (a company name we fail to
recognize) are preferable to false positives (an individual we mistake for a
company), since this tool screens BOM manufacturer/supplier fields, not people.
"""
import re

CORP_MARKERS = [
    r'\bINC\.?\b', r'\bINCORPORATED\b', r'\bLTD\.?\b', r'\bLIMITED\b', r'\bLLC\b', r'\bLLP\b', r'\bPLC\b',
    r'\bCORP\.?\b', r'\bCORPORATION\b', r'\bCO\.?\b', r'\bCOMPANY\b', r'\bCOMPANIES\b', r'\bGROUP\b',
    r'\bHOLDINGS?\b', r'\bGMBH\b', r'\bA\.?G\.?\b', r'\bS\.?A\.?\b', r'\bS\.?A\.?S\.?\b', r'\bB\.?V\.?\b',
    r'\bN\.?V\.?\b', r'\bK\.?K\.?\b', r'\bPTY\b', r'\bPTE\b', r'\bBHD\b', r'\bSDN\b',
    r'\bTECHNOLOG(Y|IES)\b', r'\bELECTRONICS?\b', r'\bINDUSTR(Y|IES|IAL)\b', r'\bMANUFACTURING\b',
    r'\bFACTORY\b', r'\bTRADING\b', r'\bINTERNATIONAL\b', r'\bENTERPRISES?\b', r'\bINSTITUTE\b',
    r'\bUNIVERSITY\b', r'\bACADEMY\b', r'\bLABORATOR(Y|IES)\b', r'\bRESEARCH\b', r'\bCENTER\b', r'\bCENTRE\b',
    r'\bFOUNDATION\b', r'\bBANK\b', r'\bAIRLINES?\b', r'\bAIRWAYS?\b', r'\bSHIPPING\b', r'\bSYSTEMS?\b',
    r'\bSOLUTIONS?\b', r'\bSERVICES?\b', r'\bBUREAU\b', r'\bCOMMITTEE\b', r'\bASSOCIATION\b', r'\bAGENCY\b',
    r'\bAUTHORITY\b', r'\bMINISTRY\b', r'\bDEPARTMENT\b', r'\bOFFICE\b', r'\bESTABLISHMENT\b',
    r'\bORGANI[SZ]ATION\b', r'\bWORKS\b', r'\bPLANT\b', r'\bMINING\b', r'\bPETROLEUM\b', r'\bCHEMICALS?\b',
    r'\bPHARMA(CEUTICAL)?S?\b', r'\bIMPORT\b', r'\bEXPORT\b', r'\bTRADE\b', r'\bOJSC\b', r'\bPJSC\b',
    r'\bJSC\b', r'\bOOO\b', r'\bZAO\b', r'\bFZE\b', r'\bFZCO\b', r'\bDMCC\b', r'\bWLL\b', r'\bEST\.?\b',
    r'\bFIRM\b', r'\bBANK(ING)?\b', r'\bAVIATION\b', r'\bAEROSPACE\b', r'\bDEFENSE\b', r'\bDEFENCE\b',
    r'\bSHIPYARD\b', r'\bCONSTRUCTION\b', r'\bENGINEERING\b', r'\bMACHINERY\b', r'\bLOGISTICS\b',
    r'\bWAREHOUSING\b', r'\bTELECOM(MUNICATIONS?)?\b', r'\bNETWORKS?\b', r'\bENERGY\b', r'\bPOWER\b',
    r'\bMETALS?\b', r'\bSTEEL\b', r'\bMINES?\b', r'\bREFINERY\b', r'\bREFINING\b', r'\bOIL\b', r'\bGAS\b',
    r'\bMOTORS?\b', r'\bAUTOMOTIVE\b', r'\bELECTRIC\b', r'\bELECTRICAL\b', r'\bSHIPS?\b', r'\bVESSEL\b',
    r'\bCONSULTING\b', r'\bCONSULTANTS?\b', r'\bMARITIME\b', r'\bFOODS?\b', r'\bTEXTILES?\b', r'\bAPPAREL\b',
]
CORP_RE = re.compile('|'.join(CORP_MARKERS), re.IGNORECASE)
HAS_LETTERS_DOT = re.compile(r'\b[A-Z]\.[A-Z]\.?\b')  # e.g. S.A., S.p.A.


def looks_like_entity(name):
    if not name:
        return False
    if CORP_RE.search(name):
        return True
    if HAS_LETTERS_DOT.search(name):
        return True
    if re.search(r'\(.*\)', name) and re.search(r'\b[A-Z]{2,}\b', name):
        # parenthetical acronym often signals an organization, e.g. "Academy ... (AASPT)"
        return True
    if re.fullmatch(r'[A-Z0-9&.\-]{3,}', name.strip()) and ' ' not in name.strip():
        # ALL-CAPS single "word" conglomerate-style name, e.g. "SINOPEC"
        return True
    return False
