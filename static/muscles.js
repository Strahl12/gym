/* Muscle-figure polygon data, vendored from react-body-highlighter
   (github.com/giavinh79/react-body-highlighter, MIT, (c) 2020 GV79).
   Two views, viewBox 0 0 1000 2000; the whole figure is these polygons.
   MUSCLE_MAP folds both our catalog's muscle names and free-exercise-db's
   into the figure's regions. Rendering lives in the page that loads this. */
const MUSCLE_SVG = {"front":[{"m":"chest","p":["518 416 510 551 580 580 678 555 706 473 620 416","298 465 314 555 408 580 482 551 478 420 376 420"]},{"m":"obliques","p":["686 633 673 571 588 596 600 641 604 833 657 788 665 698","339 784 331 718 310 633 322 571 408 592 392 633 392 837"]},{"m":"abs","p":["563 592 580 641 584 780 584 927 563 984 551 1041 514 1078 510 845 506 673 510 571","437 588 486 571 490 673 486 845 482 1073 445 1037 408 914 408 784 412 645"]},{"m":"biceps","p":["167 682 180 714 229 661 290 539 278 494 204 559","714 494 702 547 763 661 816 718 829 690 788 555"]},{"m":"triceps","p":["694 555 694 616 759 727 776 702 755 673","224 694 298 555 298 608 229 731"]},{"m":"neck","p":["555 237 506 335 506 392 616 400 706 449 694 367 633 351 584 306","290 449 302 371 363 351 412 302 445 245 490 339 486 392 380 396"]},{"m":"front-deltoids","p":["784 531 796 478 792 412 759 380 710 363 722 429 714 473","282 473 212 531 200 478 204 408 245 371 286 371 269 433"]},{"m":"head","p":["424 29 400 118 420 196 461 233 498 253 547 224 576 192 592 102 571 24 498 0"]},{"m":"abductors","p":["527 1102 543 1249 600 1102 620 1000 649 943 600 927 567 1045","478 1106 449 1253 420 1159 404 1131 396 1073 380 1024 347 939 396 922 416 992 437 1053"]},{"m":"quadriceps","p":["347 988 371 1082 371 1278 343 1371 310 1327 294 1200 282 1114 294 1008 322 947","633 1057 645 1000 669 947 702 1012 710 1118 682 1331 653 1376 624 1286 620 1114","388 1294 384 1122 412 1184 445 1294 429 1351 400 1461 363 1465 355 1400","596 1457 555 1290 608 1139 612 1302 641 1396 629 1465","327 1384 265 1457 257 1367 257 1273 269 1143 294 1335","718 1131 739 1241 739 1404 727 1457 665 1384 702 1335"]},{"m":"knees","p":["339 1400 347 1433 355 1473 363 1510 351 1567 298 1567 273 1527 273 1473 302 1441","657 1400 722 1478 722 1522 698 1571 649 1567 629 1510"]},{"m":"calves","p":["714 1604 735 1535 767 1612 796 1678 784 1878 796 1955 747 1955","249 1947 278 1649 282 1604 261 1543 249 1576 224 1616 208 1678 220 1882 208 1955","727 1951 698 1592 653 1584 641 1624 641 1653 657 1771","355 1584 359 1624 359 1669 351 1722 351 1767 322 1820 306 1873 269 1947 273 1878 282 1804 286 1755 290 1698 298 1641 302 1588"]},{"m":"forearm","p":["61 886 102 751 147 702 163 743 192 735 45 976 0 1000","845 698 833 735 800 731 951 984 1000 1004 935 894 898 763","776 722 776 776 804 841 853 898 922 1012 947 996","69 1012 135 906 188 841 216 771 212 718 49 988"]}],"back":[{"m":"head","p":["506 0 460 9 409 55 404 128 451 200 557 200 591 136 596 47 557 13"]},{"m":"trapezius","p":["447 217 477 217 472 383 477 647 383 532 353 409 311 366 391 332 438 272","523 217 557 217 566 272 609 328 689 366 647 404 617 532 523 647 532 383"]},{"m":"back-deltoids","p":["294 370 230 391 174 443 183 536 243 494 272 464","711 370 783 396 826 447 817 536 749 489 723 451"]},{"m":"upper-back","p":["311 387 281 489 285 553 340 753 472 711 472 664 366 540 336 413","689 387 719 494 715 562 660 753 528 711 528 664 634 545 664 417"]},{"m":"triceps","p":["268 498 179 557 145 723 166 817 217 638 268 557","736 502 821 557 860 732 834 821 779 630 732 557","268 583 268 685 230 753 191 774 226 655","728 583 770 647 804 774 766 753 728 689"]},{"m":"lower-back","p":["477 728 345 770 353 834 494 1021 468 830","523 728 655 770 647 834 506 1021 532 838"]},{"m":"forearm","p":["864 757 911 834 932 940 1000 1064 962 1043 881 894 843 838","136 757 89 838 68 936 0 1064 38 1043 123 885 157 830","813 796 774 779 791 847 911 1038 932 1089 945 1047","187 796 221 779 209 843 94 1030 68 1085 51 1047"]},{"m":"gluteal","p":["447 996 302 1085 298 1187 315 1260 472 1213 494 1149","553 991 511 1145 523 1209 681 1260 698 1191 694 1085"]},{"m":"adductor","p":["481 1230 447 1230 413 1255 451 1443 485 1357 489 1294","519 1226 557 1234 591 1260 549 1443 519 1362 511 1294"]},{"m":"hamstring","p":["289 1221 311 1294 366 1260 353 1353 345 1502 294 1583 289 1468 277 1413 272 1315","715 1217 694 1289 638 1260 655 1366 664 1502 711 1583 715 1477 728 1421 736 1319","387 1255 443 1460 404 1668 362 1528 370 1353","617 1255 634 1362 643 1532 600 1668 562 1464"]},{"m":"knees","p":["345 1532 311 1591 336 1664 374 1626","664 1536 630 1630 668 1664 694 1591"]},{"m":"calves","p":["294 1604 285 1672 247 1796 238 1928 255 1970 285 1932 298 1800 319 1711 319 1668","374 1651 353 1677 332 1719 311 1804 302 1919 340 2000 387 1906 391 1689","630 1651 613 1685 617 1906 664 1996 706 1919 689 1796 668 1702","706 1604 723 1685 757 1791 766 1928 745 1966 723 1936 706 1796 681 1681"]},{"m":"left-soleus","p":["285 1957 302 1957 336 2017 306 2200 285 2136 268 1983"]},{"m":"right-soleus","p":["698 1957 719 1957 736 1983 719 2132 702 2196 672 2021"]}]};
const MUSCLE_MAP = {
  // our catalog vocabulary
  chest: ["chest"], biceps: ["biceps"], triceps: ["triceps"],
  forearms: ["forearm"], abdominals: ["abs", "obliques"],
  shoulders: ["front-deltoids", "back-deltoids"], traps: ["trapezius"],
  upper_back: ["upper-back", "trapezius"], lats: ["upper-back"],
  lower_back: ["lower-back"], glutes: ["gluteal"],
  quadriceps: ["quadriceps"], hamstrings: ["hamstring"],
  calves: ["calves", "left-soleus", "right-soleus"],
  adductors: ["adductor"], abductors: ["abductors"], neck: ["neck"],
  full_body: [], cardio: [], other: [],
  // free-exercise-db vocabulary (where it differs)
  "middle back": ["upper-back"], "lower back": ["lower-back"],
  abs: ["abs", "obliques"], forearm: ["forearm"],
  // gymvisual dataset vocabulary (where it differs)
  pectorals: ["chest"], delts: ["front-deltoids", "back-deltoids"],
  quads: ["quadriceps"], spine: ["lower-back"],
  "hip flexors": ["quadriceps"], "serratus anterior": ["obliques"],
  "levator scapulae": ["neck", "trapezius"], "cardiovascular system": [],
};

/* Shared figure renderer for the Review/Stats body maps.
   opts: { primary: [..], secondary: [..] }          — union colouring, or
         { heat: {muscle: score}, max: number }       — gradient colouring
         { onPick: fn(muscle) }                       — tappable regions
   Muscle names use the app's catalog vocabulary; REGION_TO_MUSCLE inverts
   MUSCLE_MAP for the catalog keys so taps report catalog names. */
const MUSCLE_VOCAB = ["chest", "biceps", "triceps", "forearms", "abdominals",
  "shoulders", "traps", "upper_back", "lats", "lower_back", "glutes",
  "quadriceps", "hamstrings", "calves", "adductors", "abductors", "neck"];
const REGION_TO_MUSCLE = (() => {
  const out = {};
  MUSCLE_VOCAB.forEach(m => (MUSCLE_MAP[m] || []).forEach(r => {
    if (!(r in out)) out[r] = m;
  }));
  return out;
})();

function muscleFigure(view, opts) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 1000 2210");
  svg.setAttribute("class", "mfig");
  const fold = ns => {
    const out = new Set();
    (ns || []).forEach(m => (MUSCLE_MAP[(m || "").replace(/ /g, "_")] ||
                             MUSCLE_MAP[m] || []).forEach(x => out.add(x)));
    return out;
  };
  const prim = fold(opts.primary), sec = fold(opts.secondary);
  MUSCLE_SVG[view].forEach(part => part.p.forEach(pts => {
    const pg = document.createElementNS(NS, "polygon");
    pg.setAttribute("points", pts);
    const muscle = REGION_TO_MUSCLE[part.m];
    if (opts.heat) {
      const s = (muscle && opts.heat[muscle]) || 0;
      if (s > 0 && opts.max > 0) {
        pg.setAttribute("class", "is-heat");
        const t = 0.15 + 0.85 * (s / opts.max);
        pg.setAttribute("fill-opacity", t.toFixed(2));
        pg.setAttribute("stroke-opacity", Math.min(1, t + 0.2).toFixed(2));
      }
    } else {
      if (prim.has(part.m)) pg.setAttribute("class", "is-prim");
      else if (sec.has(part.m)) pg.setAttribute("class", "is-sec");
    }
    if (opts.onPick && muscle) {
      pg.style.cursor = "pointer";
      pg.addEventListener("click", () => opts.onPick(muscle));
    }
    svg.appendChild(pg);
  }));
  return svg;
}
