# Ek Parchi scan app (MVP)

**One photo of a ticked OP slip becomes the lab order.** Team InnoVerse, SEVA FIRST Innovation Challenge 2026 (REG-003-OP).

This is a working software MVP of the Ek Parchi collection counter. It runs on any laptop with Python, fully offline.

| Step | What the app does |
|---|---|
| 1. Slip | `make-slip` prints an A5 Ek Parchi slip with a QR code (OP number, name, age/sex), 12 test tick-boxes, consent boxes and four corner marks |
| 2. Photo | A webcam or phone photo of the ticked slip |
| 3. Straighten | OpenCV finds the four black corner marks and straightens the photo (perspective transform); a slip photographed upside down is turned using the QR |
| 4. Read | The QR gives the OP number and name; each tick-box is read by the share of ink inside it, against two cut-offs: ticked / empty / unsure |
| 5. Check | Unsure marks, and any box with an X just left of it (the "wrong tick" rule), go to a 1-tap check (`-i`) |
| 6. Order | The order is saved in a local SQLite lab database with one sample number per tube type, and tube labels are printed as images (name, OP number, sample number, QR, tube type) |
| 7. Tube lock | `tube` scans each tube label against the patient on screen: a wrong tube gives a red MISMATCH, and the next slip cannot be scanned until every tube matches |

Every step is written to an append-only `sync_log` table, ready to push to the hospital HMIS when online.

## Run it

```bash
pip install -r requirements.txt

# 1. Make and print a slip (A5 landscape, 100% scale)
python ekparchi.py make-slip --op 2026-104857 --name "Ramesh Kumar" --age 60 --sex M

# 2. Tick tests with a dark pen, take a photo of the whole slip, then:
python ekparchi.py scan photo.jpg -i      # -i asks y/n for each unsure box (the 1-tap check)

# 3. Scan each tube label (photo of the label, or type the printed number)
python ekparchi.py tube --image out/ORD-.../label_EP-XXXXXX-A.png
python ekparchi.py tube --code EP-XXXXXX-B

# Self-test on synthetic, distorted photos
python ekparchi.py selftest --n 100
```

Outputs go to `out/<order id>/`: `order.json`, `label_*.png`, `straightened_slip.jpg` and `collection_screen.html` (patient name in large text, tube checklist, boxes that need a check, and a Next button that stays locked until every tube is scanned).

Try it without printing: `python ekparchi.py scan out/sample_photo.jpg` reads the included sample photo (CBC and blood sugar ticked, CRP cancelled with an X, WhatsApp consent ticked).

## Self-test results (synthetic photos)

`python ekparchi.py selftest --n 100 --seed 7` makes 100 photos per camera set-up from the slip, with random ticks (checks, crosses, slashes, scribbles), black, blue and faint pens, about 8% of ticks cancelled with an X, and random tilt, rotation, shadow, dim light, noise, blur and JPEG compression.

| Camera set-up | Corner marks found | QR read | Taps per slip | Slips fully right after the check |
|---|---|---|---|---|
| Counter stand + lamp | 100% | 100% | 0.22 | 100% |
| Handheld phone | 100% | 100% | 0.79 | 99% |
| Hard: dim, shadow, blur, steep tilt | 83% | 78% | 1.35 | 77% |

No photo ever returned a wrong OP number; when the QR could not be read the app asks for a retake. These are synthetic photos made by this script, so they test the method, not real-world accuracy. The next step is a bench test on 200 real hand-ticked slips (target: 99%+ fully right after the check).

## Files

- `ekparchi.py`: the whole app (slip, reader, local lab database, labels, tube lock, self-test)
- `config.json`: the 12 tests, their tube types and the ink cut-offs. The test list and tube types are a starting point for the clinical mentor to confirm with doctors and the lab in-charge.
- `out/`: a sample slip (PDF + PNG), a sample photo, one sample order, self-test photos and the self-test report

## Limits of this MVP

- Tube labels carry a QR code (a 2D barcode) with the sample number; a 1D barcode printer driver comes with the label printer in the build phase.
- The collection screen is a generated web page; the counter app with a live camera feed and touch buttons is built in Day 8-30.
- No analyser link (ASTM/HL7) or ABHA sharing yet; those are stretch goals for the pilot.
- No real patient data is used. Names and numbers in the samples are made up.

License: MIT.
