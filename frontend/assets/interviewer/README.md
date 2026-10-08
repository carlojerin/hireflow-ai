# AI interviewer image

`interviewer.svg` is the fixed interviewer image shown on the AI Interview V2 page
(head-and-shoulders, frontal, business attire, neutral studio background).

**It is a neutral illustrated PLACEHOLDER, not a photograph.** No photo was downloaded or generated.

## Replacing it with the final approved portrait
1. Put the approved image in this folder (e.g. `interviewer.jpg` — square or 4:5, head-and-shoulders,
   frontal, direct eye contact, neutral background, no text/watermark).
2. Change the one constant `INTERVIEWER_IMAGE_SRC` in `frontend/js/interview-visual-state-v2.js`
   to `./assets/interviewer/interviewer.jpg`.

Nothing else needs to change. The identity is fixed: the candidate cannot pick a different image.
