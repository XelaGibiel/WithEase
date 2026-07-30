"""Guided reading (enrollment) prompts.

The user reads these known sentences aloud; we store (audio, exact text) pairs
as *gold* training data, so a later fine-tuning can adapt the model to the
user's voice.  The sentences are phonetically varied and include numbers,
punctuation and everyday plus a few technical words.
"""
from __future__ import annotations

PROMPTS: list[str] = [
    "Guten Tag, mein Name ist heute gut gelaunt und bereit zum Diktieren.",
    "Die Sonne scheint hell über den grünen Wiesen und dunklen Wäldern.",
    "Am fünfzehnten März zahlte ich dreihundertzwanzig Euro und neun Cent.",
    "Zwölf zähe Zwerge zogen zügig zur alten Zugbrücke am Zaun.",
    "Können Sie mir bitte kurz erklären, wie dieses Programm funktioniert?",
    "Ich schreibe eine E-Mail an Frau Müller über das nächste Projekt.",
    "Der schnelle braune Fuchs springt faul über den schlafenden Hund.",
    "Wir treffen uns um Viertel nach acht vor dem großen Bahnhof.",
    "Physik, Chemie und Mathematik waren meine liebsten Schulfächer.",
    "Bitte speichern, kopieren und schließen Sie anschließend das Fenster.",
    "Österreich, Ölgemälde und Übung brauchen die Umlaute ä, ö und ü.",
    "Die Straße war nass, doch der Regen hörte gegen Mittag endlich auf.",
    "Vierzehn Vögel flogen fröhlich über das weite, offene Feld hinweg.",
    "Herzlichen Glückwunsch, das hast du wirklich ganz ausgezeichnet gemacht.",
]
