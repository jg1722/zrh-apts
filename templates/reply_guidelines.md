# Reply / follow-up guidelines (PLACEHOLDER — defer to the user's style)

Used when a landlord replies. **Match the language of the landlord's reply** —
if they replied in German, reply in German (formal "Sie"); if English, reply
in English. If mixed, mirror the language they used most. Concise and warm.

## Always
1. **Answer** any question the landlord raised (occupants, move-in date, pets,
   guarantor, etc.) — briefly and directly.
2. **Attach the dossier** only if requested from `dossier/` (all PDFs).
3. **Propose 2–3 concrete viewing slots** with weekday + date + time, and offer, (validate these slots first with the user)
   flexibility ("oder ein anderer Termin nach Ihrer Wahl").
4. Keep it short. One screen, no padding.

## Skeleton

**Subject:** Re: {their_subject}

Sehr geehrte/r {contact_name},

vielen Dank für Ihre Rückmeldung. {answers_to_their_questions}

Anbei finden Sie mein vollständiges Bewerbungsdossier. Für eine Besichtigung
würde mir Folgendes passen:

- {slot_1}
- {slot_2}
- {slot_3}

Gerne richte ich mich auch nach einem anderen Termin Ihrerseits.

Freundliche Grüsse
{full_name}
{phone}

## After sending
Update the status: `scripts/set_status.py <id> viewing`.
Once a viewing is booked or the flat is gone, move to `closed` or `rejected`.
