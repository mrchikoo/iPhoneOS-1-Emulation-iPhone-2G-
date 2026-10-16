// keyboard.js - Mac keyboard -> taps on the guest's on-screen keyboard (iPhone OS 1.0).
// ES module, no dependencies. The emulator has no keyboard device, only touch contacts, so each Mac
// keystroke is translated into one or two short taps (pointer-style down, ~50 ms, up) on the guest's own
// software keyboard, switching between its letter / number / symbol layouts and the shift key as needed.
// Coordinates are guest canvas coordinates (320x480), measured from screenshots (see README, "Keyboard").
//
// The guest keyboard layout is read back from the canvas pixels (a coarse 40x25 grayscale signature of the
// y=276..475 band is compared with reference signatures taken from real screenshots), so we always know
// whether letters, numbers or symbols are showing, whether it is the Safari URL keyboard (. / .com) or
// the plain one (space + Return), and whether shift is lit.
//
// Typing mode:
//   - automatic: whenever the on-screen keyboard is detected, Mac keys are typed into the phone;
//   - Tab forces typing mode on (even if detection fails) and, while typing, Tab turns it off;
//   - Esc leaves typing mode (until the keyboard goes away and comes back, or Tab is pressed);
//   - while typing, the phone shortcuts (H, P, M, +, -, Esc as Home) are not triggered.
const SIGS = {
"ul": "yfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycmZn8qorK2kypqvysqXmcrKn5/Kyqusycq0tMrK1dXKyp+ZysqfnMrChm7FvURKucVxucXFbnPFxby8xcWylsXFhobFxby8xcWEh8LFc5XFwK6ZwMC7vMDAq6PAwMTGwMDX18DA3NHAwLGvwMDX18DAtK7AwMTqwJ+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+HhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHhYWQoaGQkKGhkJChoZCQoaGQkKGhkJChoZCQoaGQkKGhkJChoZCFhYKCw/T0w8P09MPD9PTCwvT0w8P09MPC9PTDw/T0w8P09MPD9PTDgoJ+fr+kpL+/f5O/v3h6vr+Hmr+/g5a/v4iIv7/wlL+/e5u/v5Tvv35+e3u6bWy6uo9aurpyb7i6fLy6unZMurptbbq6pX26umN/urp9ubp7e3d3tdfXtbXY07S1z9e0tNfjtLXVzLW119e1tdTVtbTX1LW11M21d3d0dIKYmIODmJiDg5iYg4KYmIODmJiDgpiYgoOYmIKDmJiDg5iYgnR0cHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHuJiYmCbY2vr42Nr6+Nja+vjY2vr42Nr6+Nja+vjY2vr41tgomJiXuBlauXjGm11tO1ttzdtbXc2La23t62tdXbtrXd4LW02tuzaY2VnJuBeaynwIVlsq1psrKJirKydaOysoCAsrJdXLKyUXKypEJNnGWE08vZeXKLtap7Yq1mlq2th4KtrXyGra2VnK2td3atrZJcraJkZ5xie7XCyHJqd3d3cV+n29unp9vbp6fc3Ken29unp9vbp6fb26en29unX3B2d3dqWFdXV1dcYW5uYWFubmFhbm5hYW5uYWFubmFhbm5hYW5uYVxXV1dXWFpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlprf39/f39/f39rj8DAwMC/Z7jAwMDAqnXAwMDAwI9mdnZ2dnZ2dnZma39+gH5+fX5/a6Ta2tra2m/a2trA2seC2tra2tqkYnFxcYJxcXFxYmR1pKinnKajdWSe0dLR0tJr0dHBn9K/fruafYVpm1pmZoiel4dmZlpcaYOMf5CNhGlbl8fHo8fHZ8fHmsbHtnisnJitnZVTW1tgiXpuW1tTUF1dXV1cXV1cUYi8vLy8u120vLy8vKRsvLy8vLyISlFRUVFRUVFRSQ==",
"un": "yfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycnUwcrKuazKyrivysrpssrKur3Kyr26ysq7osrKtrbKyrGwysqyssrF3pbFxbimxcXNg8XFhnjFxaWExcV6isXFzrfFxYWGxcWZdsXFkJDFwOfJwMCxrsDAwL3AwOrJwMDAvMDAxb3AwMvjwMC7vMDAwMTAwMPDwJ+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+HhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHhYWQoaGhoZCQoaGhoZCQoaGhoZCQoaGhoZCQoaGhoZCQoaGhoZCFhYKCw/T09PTDw/T09PTDw/T09PTDw/T09PTDw/T09PTDw/T09PTDgoJ+fr/uf33wv7/wqZXwv7/Uk7zwv7/wqYTwv7/w8PDwv7/wwr3wv35+e3u65WR26rq66oBR6bq66KF+zbq66syy6rq66tzY6rq66rax6rp7e3d3tePX1uO0tePX0eC0tePZ0+K0tePd3eO0tePVzOO1tOPj4+O1d3d0dIKYmJiYg4OYmJiYg4OYmJiYg4KYmJiYg4KYmJiYgoOYmJiYgnR0cHBwcHBwcHBwcHBwcHBwcHFwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHuJiYmCbY2vr6+vr42Nr6+vr6+Nja+vr6+vjY2vr6+vr41tgomJiXuBlauXjGm16+zr6+u2tevs6+vrtrXr6+vr67a16+vr6+u2aY2VnJuBeaynwIVlsujo6OjosrLo6L3o6LKy6OGu4eiysujhg9nosmWE08vZeXKLtap7Yq3i4uLi4q2t4uK44uKtreLfyd/ira3i357c4q1ie7XCyHJqd3d3cV+n276Nvtunp9vb29vbp6fb29vb26en29vb29unX3B2d3dqWFdXV1dcYW5ubm5uYWFubm5ubmFhbm5ubm5hYW5ubm5uYVxXV1dXWFpaWlpaWlpaWlpaWlpZWlpaWlpaWlpaWVpaWlpaWlpaWlpaWlpaWlprf39/f39/f39rj8DAwMC/Z7jAwMDAqnXAwMDAwI9mdnZ2dnZ2dnZma39/f39+gH5/a6Ta2tra2m/a2trA2seC2tra2tqkYnFxcYJxcXFxYmR1dcawsauFdWSe0dLR0tJr0dHBn9K/fruafYVpm1pmZoiel4dmZlpcaXWPlomUeWlbl8fHo8fHZ8fHmsbHtnisnJitnZVTW1tgiXpuW1tTUF1dXV1cXV1cUYi8vLy8u120vLy8vKRsvLy8vLyISlFRUVFRUVFRSQ==",
"us": "yfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycnUwcrKuazKyrivysrpssrKur3Kyr26ysq7osrKtrbKyrGwysqyssrF3pbFxbimxcXNg8XFhnjFxaWExcV6isXFzrfFxYWGxcWZdsXFkJDFwOfJwMCxrsDAwL3AwOrJwMDAvMDAxb3AwMvjwMC7vMDAwMTAwMPDwJ+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+HhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHhYWQoaGhoZCQoaGhoZCQoaGhoZCQoaGhoZCQoaGhoZCQoaGhoZCFhYKCw/Ty8/TDw/Tx8PTDw/T09PTDw/T09PTDw/T09PTDw/T09PTDgoJ+fr/wmKrwv7/we5nwv7/wpqDwv7/w0b3wv7/w3Lrwv7/ws7Pwv35+e3u66uLi6rq66pFg6rq66pSZ6rq66tTC6rq66urq6rq66tfX6rp7e3d3tePj4+O0tePT0OO0tePb3OO0tePf3OO0tePj4+O1tOPj4+O1d3d0dIKYmJiYg4OYmJiYg4OYmJiYg4KYmJiYg4KYmJiYgoOYmJiYgnR0cHFxcXBwcHBwcHBwcHBwcHFwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHyQlZOFbY2vr6+vr42Nr6+vr6+Nja+vr6+vjY2vr6+vr41tgomJiXuKt8i/nmq16+zr6+u2tevs6+vrtrXr68br67a16+vH6+u2aY2VnJuBisb126FosujlyePosrLo6L3o6LKy6OiO6OiysujojejosmWE08vZeYGy5cOUZa3i27rd4q2t4uKh4uKtreLijeLira3i4ozi4q1ie7XCyHJwjZmTfGCn29vb29unp9vb2tvbp6fb28/b26en29vO29unX3B2d3dqWVpdXFhcYW5ubm5uYWFubm5ubmFhbm5ubm5hYW5ubm5uYVxXV1dXWFpaWlpaWlpaWlpaWlpZWlpaWlpaWlpaWVpaWlpaWlpaWlpaWlpaWlprf39/f39/f39rj8DAwMC/Z7jAwMDAqnXAwMDAwI9mdnZ2dnZ2dnZma39/f39+gH5/a6Ta2tra2m/a2trA2seC2tra2tqkYnFxcYJxcXFxYmR1dcawsauFdWSe0dLR0tJr0dHBn9K/fruafYVpm1pmZoiel4dmZlpcaXWPlomUeWlbl8fHo8fHZ8fHmsbHtnisnJitnZVTW1tgiXpuW1tTUF1dXV1cXV1cUYi8vLy8u120vLy8vKRsvLy8vLyISlFRUVFRUVFRSQ==",
"pl": "yfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycmZn8qorK2kypqvysqXmcrKn5/Kyqusycq0tMrK1dXKyp+ZysqfnMrChm7FvURKucVxucXFbnPFxby8xcWylsXFhobFxby8xcWEh8LFc5XFwK6ZwMC7vMDAq6PAwMTGwMDX18DA3NHAwLGvwMDX18DAtK7AwMTqwJ+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+HhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHhYWQoaGQkKGhkJChoZCQoaGQkKGhkJChoZCQoaGQkKGhkJChoZCFhYKCw/T0w8P09MPD9PTCwvT0w8P09MPC9PTDw/T0w8P09MPD9PTDgoJ+fr+kpL+/f5O/v3h6vr+Hmr+/g5a/v4iIv7/wlL+/e5u/v5Tvv35+e3u6bWy6uo9aurpyb7i6fLy6unZMurptbbq6pX26umN/urp9ubp7e3d3tdfXtbXY07S1z9e0tNfjtLXVzLW119e1tdTVtbTX1LW11M21d3d0dIKYmIODmJiDg5iYg4KYmIODmJiDgpiYgoOYmIKDmJiDg5iYgnR0cHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHuJiYmCbY2vr42Nr6+Nja+vjY2vr42Nr6+Nja+vjY2vr41tgomJiXuBlaKVjGm11tO1ttzdtbXc2La23t62tdXbtrXd4LW02tuzaY2VnJuBeaOis4Rlsq1psrKJirKydaOysoCAsrJdXLKyUXKypEJNnGWE08vZeXKKrKV7Yq1mlq2th4KtrXyGra2VnK2td3atrZJcraJkZ5xie7XCyHJqd3d3cV+n29unp9vbp6fc3Ken29unp9vbp6fb26en29unX3B2d3dqWFdXV1dcYW5uYWFubmFhbm5hYW5uYWFubmFhbm5hYW5uYVxXV1dXWFpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlprf39/f39/f39rj8DAwMDAwMDAwMDAwMDAwMDAwI9mdnZ2dnZ2dnZma39/f35+fX5/a6Ta2tra2tra2tra2tra2tra2tqkYnV+cXFxcXdxYmR1e7Kon6egdWSe0dLR0dLRoKqjoLWczdLR0dHSnlqZmpSdl5+ulFpcaXh5f5GNgmlbl8fHx8fHxq+ksbSyscbHx8fHx5dTboR8d3qqeHdTUF1dXV1cXV1cUYi8vLy8vLy8vLy8vLy8vLy8vLyISlFRUVFRUVFRSQ==",
"pn": "yfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycnUwcrKuazKyrivysrpssrKur3Kyr26ysq7osrKtrbKyrGwysqyssrF35bFxbimxcXOhMXFh3jFxaWExcV7isXFzrfFxYaGxcWZdsXFkJDFwOfKwMCyr8DAwb3AwOrKwMDAvcDAxr7AwMvjwMC7vMDAwcTAwMPDwJ+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+HhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHkKGhkJChoZCQoaGQkKGhkJChoZCQoaGQkKGhkJChoZCQoaGQkKGhkMP09MPD9PTDw/T0w8P09MPC9O7Dw/Dyw8Lx8MPD9PTDw/T0w8P09MK/8PC/v+ysv7/c1b+/3NW/v77Iv7/dqL+/eJ2/v5qjv76Ceb2/r7C/urS0urqv27q619G6utfRurqi1bq655C6uo1iurpxX7q5Zne5uurqurXj47W12eO0tePktbTa07S12Me1tNHNtbTS0bS109K0tNnVtbTj47SDmJiDg5iYgoOYmIODmJiDg5iYg4OYmIKCmJiCg5iYg4KYmIKDmJiCcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHuJiYmCbY2vr6+udaevr6+efa6vr659nq+vr6d1rq+vr41tgomJiXuBlJWVjGm16+vr64Dr6+vr15Tr4NfrlNfr3evrgOzr3+u2aY2VnJuBebejlohlsujn5+d85+jn59OQ57Rz55HT6JLn6Hzn57PnsmWE08vZeXKei4+EYq3iwdnieeLiseLOjOLQuuKMzuKs4uJ54uLi4q1ie7XCyHJqd3d3cV+n29vb23Ta28rbx4fb29vbh8fb29vadNvb29unX3B2d3dqWFdXV1dcYW5ubm1aaG5ubmZdbm5ubl1mbm5uaFptbm5uYVxXV1dXWFpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlprf39/f39/f39rj8DAwMDAwMDAwMDAwMDAwMDAwI9mdnZ2dnZ2dnZma39/f39+gH5/a6Ta2tra2tra2tra2tra2tra2tqkYnV+cXFxcXdxYmR1dcawsauFdWSe0dLR0dLRoKqjoLWczdLR0dHSnlqZmpSdl5+ulFpcaXWPlomUeWlbl8fHx8fHxq+ksbSyscbHx8fHx5dTboR8d3qqeHdTUF1dXV1cXV1cUYi8vLy8vLy8vLy8vLy8vLy8vLyISlFRUVFRUVFRSQ==",
"ps": "yfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycn7+8nJ+/vJyfv7ycms1srK1qzKytC3ysq50srKz8rKtrDPysrNzcrKv77Kyuztysrv7crFlunFxemWxcWozcXFzajFxYqExbmclK3FycnFxdPSxcWkpsXFmInFwKnNwMDMqcDAzLTAwLPLwMDT08DA0r63wOrqwMDq6cDA5eXAwOrqwJ+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+furqfn7q6n5+6up+HhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHh4aGh4eGhoeHhoaHkKGhkJChoZCQoaGQkKGhkJChoZCQoaGQkKGhkJChoZCQoaGQkKGhkMP09MPD9PTDw+/ow8P09MPC9PTDw/T0w8L09MPD9PTDw/T0w8P09MK/8PC/v6/pv7/Zu7+/8O+/v+XFv7/F5b+/ka6/v6Szv7+vsb+/1Mu/uurqurrgqrq61Le6ur29urqLuLq6uYu6uoPAurqHw7q6oqK6uranurW7u7W149m0tdfGtbTj47S14+O1tOPjtbTf07S12c61tN/ftbTj47SDmJiDg5iYgoOYmIODmJiDg5iYg4OYmIKCmJiCg5iYg4KYmIKDmJiCcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHuJiYmCbY2vr6+udaevr6+efa6vr659nq+vr6d1rq+vr41tgomJiXuBlZOUjGm16+vr64Dr6+vr15Tr4NfrlNfr3evrgOzr3+u2aY2VnJuBeqq2r4plsujn5+d85+jn59OQ57Rz55HT6JLn6Hzn57PnsmWE08vZeXKTo52CYq3iwdnieeLiseLOjOLQuuKMzuKs4uJ54uLi4q1ie7XCyHJqd3d3cV+n29vb23Ta28rbx4fb29vbh8fb29vadNvb29unX3B2d3dqWFdXV1dcYW5ubm1aaG5ubmZdbm5ubl1mbm5uaFptbm5uYVxXV1dXWFpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlpaWlprf39/f39/f39rj8DAwMDAwMDAwMDAwMDAwMDAwI9mdnZ2dnZ2dnZma39/f39+gH5/a6Ta2tra2tra2tra2tra2tra2tqkYnV+cXFxcXdxYmR1dcawsauFdWSe0dLR0dLRoKqjoLWczdLR0dHSnlqZmpSdl5+ulFpcaXWPlomUeWlbl8fHx8fHxq+ksbSyscbHx8fHx5dTboR8d3qqeHdTUF1dXV1cXV1cUYi8vLy8vLy8vLy8vLy8vLy8vLyISlFRUVFRUVFRSQ=="
};

const W = 40, H = 25, BAND_Y = 276;
const TAP_MS = 50;          // finger down -> up
const GAP_MS = 110;         // after an ordinary key tap, before the next one (lets the guest redraw)
const SETTLE_MAX_MS = 900;  // wait for a layout switch to show up on screen
const POLL_MS = 300;        // keyboard auto-detection
const SHIFT_ON = 168;       // mean luma of the shift key area: ~149 off, ~188 lit
const MATCH_MAX = 7;        // mean abs signature difference above which "no keyboard is showing"

const sleep = ms => new Promise(r => setTimeout(r, ms));
const unb64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const REF = {};
for (const [k, v] of Object.entries(SIGS)) REF[k] = unb64(v);
const NAMES = {ul:'url/letters', un:'url/nums', us:'url/sym', pl:'plain/letters', pn:'plain/nums', ps:'plain/sym'};

// ---- key tables (guest coordinates) --------------------------------------------------------------
const ROW_Y = [296, 349, 402, 457];
const LETTERS = {};
'qwertyuiop'.split('').forEach((c, i) => LETTERS[c] = [16 + 32 * i, ROW_Y[0]]);
'asdfghjkl'.split('').forEach((c, i) => LETTERS[c] = [32 + 32 * i, ROW_Y[1]]);
'zxcvbnm'.split('').forEach((c, i) => LETTERS[c] = [64 + 32 * i, ROW_Y[2]]);
const DIGITS = {};
'1234567890'.split('').forEach((c, i) => DIGITS[c] = [16 + 32 * i, ROW_Y[0]]);
const row = (chars, x0, step, y) => Object.fromEntries([...chars].map((c, i) => [c, [x0 + step * i, y]]));
const SWITCH = [40, 457];   // @123 / .?123 (from letters) and ABC (from numbers / symbols)
const SHIFT = [21, 402];    // shift (letters), "#+=" / shift (numbers), "123" / shift (symbols)
const BACKSPACE = [302, 402];
const RETURN = [280, 457];  // Go / Return / Search / ...
const SPACE = [160, 457];   // plain keyboard only

// layout -> { char: [x, y] }
const MAPS = {
  url: {
    letters: {...LETTERS, '.': [106, 457], '/': [160, 457]},
    nums: {...DIGITS, '@': [40, 349], '&': [89, 349], '%': [136, 349], '?': [184, 349], ',': [233, 349], '=': [280, 349],
           '_': [75, 402], ':': [131, 402], '-': [188, 402], '+': [244, 402], '.': [106, 457], '/': [160, 457]},
    sym: {...DIGITS, '*': [40, 349], '$': [88, 349], '#': [136, 349], '!': [184, 349], "'": [233, 349], '^': [280, 349],
          '~': [75, 402], ';': [131, 402], '(': [188, 402], ')': [244, 402], '.': [106, 457], '/': [160, 457]},
  },
  plain: {
    letters: {...LETTERS},
    nums: {...DIGITS, ...row('-/:;()$&@"', 16, 32, 349), ...row('.,?!\'', 70, 45, 402)},
    sym: {...DIGITS, ...row('[]{}#%^*+=', 16, 32, 296), ...row('_\\|~<>', 16, 32, 349), ...row('.,?!\'', 70, 45, 402)},
  },
};
// plain-keyboard sym layout has digits on row 1? No: row 1 is [ ] { } # % ^ * + =, so drop the digit entries there.
for (const d of '1234567890') if (!'[]{}#%^*+='.includes(d)) delete MAPS.plain.sym[d];
// '-' on the plain number layout is an en-dash-looking hyphen key; same key.

// ---- pixel reading -------------------------------------------------------------------------------
function readLayout(ctx) {
  let d;
  try { d = ctx.getImageData(0, BAND_Y, 320, 200).data; } catch { return null; }
  const sig = new Uint8Array(W * H);
  for (let by = 0; by < H; by++) for (let bx = 0; bx < W; bx++) {
    let s = 0;
    for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
      const i = ((by * 8 + y) * 320 + bx * 8 + x) * 4;
      s += d[i] * 0.3 + d[i + 1] * 0.59 + d[i + 2] * 0.11;
    }
    sig[by * W + bx] = Math.round(s / 64);
  }
  let best = null, bestD = 1e9;
  for (const k in REF) {
    let s = 0; const r = REF[k];
    for (let i = 0; i < sig.length; i++) s += Math.abs(sig[i] - r[i]);
    s /= sig.length;
    if (s < bestD) { bestD = s; best = k; }
  }
  if (bestD > MATCH_MAX) return null;
  return {kind: best[0] === 'u' ? 'url' : 'plain',
          layout: {l:'letters', n:'nums', s:'sym'}[best[1]], name: NAMES[best], dist: bestD};
}
function shiftLit(ctx) {
  let d;
  try { d = ctx.getImageData(4, 390, 34, 26).data; } catch { return false; }
  let s = 0;
  for (let i = 0; i < d.length; i += 4) s += d[i] * 0.3 + d[i + 1] * 0.59 + d[i + 2] * 0.11;
  return s / (d.length / 4) > SHIFT_ON;
}

// ---- module ----------------------------------------------------------------------------------------
export function initKeyboard({canvas, context, status, isReady, send, others, touched, setStatus}) {
  let forced = false, suppressed = false, visible = false, busy = false, dropped = 0;
  const queue = [];
  let last = null;

  const note = document.createElement('p');
  note.id = 'kb-status';
  note.setAttribute('role', 'status');
  note.style.cssText = 'margin:2px 0 0;font:12px/1.3 -apple-system,system-ui,sans-serif;color:#6b6b6b;text-align:center';
  (status || canvas).after(note);

  const active = () => forced || (visible && !suppressed);
  function paint() {
    let text;
    if (active()) text = 'Keyboard: typing on' + (last ? ' (' + last.name + ')' : '') + ' - Esc or Tab to leave, H/P/M/+/- shortcuts paused';
    else if (visible) text = 'Keyboard: off - press Tab to type with your Mac keyboard';
    else text = 'Keyboard: opens when the phone shows its on-screen keyboard (Tab to force)';
    if (note.textContent !== text) note.textContent = text;
    note.dataset.typing = String(active());
    document.body.dataset.typing = String(active());
  }

  // Tap one guest point: contact down, ~50 ms, contact up.
  async function tap([x, y]) {
    const used = new Set(others().map(c => c.id));
    const id = [5, 4, 3, 2, 1].find(n => !used.has(n));
    if (!id) return;
    touched(); send([...others(), {id, x, y}]);
    await sleep(TAP_MS);
    touched(); send(others());
  }
  const look = () => readLayout(context);

  // Tap `pt`, then wait (polling) until the keyboard layout/shift looks different, or timeout.
  async function tapAndSettle(pt) {
    const before = look(), bs = shiftLit(context);
    await tap(pt);
    const t0 = performance.now();
    while (performance.now() - t0 < SETTLE_MAX_MS) {
      await sleep(40);
      const now = look();
      if (!now || !before || now.name !== before.name || shiftLit(context) !== bs) { await sleep(40); return; }
    }
  }

  async function ensureLayout(want, kind) {
    for (let tries = 0; tries < 4; tries++) {
      const cur = look();
      if (!cur) { await sleep(150); continue; }
      if (cur.layout === want) return cur;
      if (want === 'letters') await tapAndSettle(SWITCH);                 // ABC / ?123 slot returns to letters
      else if (cur.layout === 'letters') await tapAndSettle(SWITCH);      // -> nums
      else if (cur.layout === 'nums' && want === 'sym') await tapAndSettle(SHIFT);
      else if (cur.layout === 'sym' && want === 'nums') await tapAndSettle(SHIFT);
      else await tapAndSettle(SWITCH);
    }
    return null;
  }
  async function ensureShift(upper) {
    for (let tries = 0; tries < 2; tries++) {
      if (shiftLit(context) === upper) return true;
      await tapAndSettle(SHIFT);
    }
    return shiftLit(context) === upper;
  }

  // Type one character (or a special: '\b' backspace, '\n' return, ' ' space).
  async function typeChar(ch) {
    let cur = look();
    if (!cur) { await sleep(200); cur = look(); }
    if (!cur) { dropped++; return false; }
    if (ch === '\b') { await tap(BACKSPACE); await sleep(GAP_MS); return true; }
    if (ch === '\n') { await tap(RETURN); await sleep(GAP_MS + 60); return true; }
    if (ch === ' ') {
      if (cur.kind === 'url') { dropped++; return false; }   // the Safari URL keyboard has no space key
      await tap(SPACE); await sleep(GAP_MS); return true;
    }
    const lower = ch.toLowerCase();
    const isLetter = lower !== ch.toUpperCase() && LETTERS[lower];
    let want, pt, upper = false;
    if (isLetter) { want = 'letters'; pt = LETTERS[lower]; upper = ch !== lower; }
    else {
      // prefer the layout we are already on if it has the key, else nums, then sym
      const m = MAPS[cur.kind];
      const order = [cur.layout, 'nums', 'sym', 'letters'];
      want = order.find(l => m[l][ch]);
      if (!want) { dropped++; return false; }
      pt = m[want][ch];
    }
    cur = await ensureLayout(want);
    if (!cur) { dropped++; return false; }
    if (want === 'letters') await ensureShift(upper);
    await tap(MAPS[cur.kind][want][isLetter ? lower : ch] || pt);
    await sleep(GAP_MS);
    return true;
  }

  async function pump() {
    if (busy) return;
    busy = true;
    try {
      while (queue.length) {
        if (!isReady()) { queue.length = 0; break; }
        const ch = queue.shift();
        await typeChar(ch);
      }
    } catch (e) { console.error('keyboard.js', e); queue.length = 0; }
    finally { busy = false; paint(); }
  }
  const enqueue = ch => { if (queue.length < 500) queue.push(ch); pump(); };

  // ---- Mac keyboard events --------------------------------------------------------------------------
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target && event.target.closest && event.target.closest('input,textarea,select,[contenteditable="true"]')) return;
    const swallow = () => { event.preventDefault(); event.stopImmediatePropagation(); };
    if (event.key === 'Tab') {
      swallow();
      if (active()) { forced = false; suppressed = true; } else { forced = true; suppressed = false; }
      paint(); return;
    }
    if (!active()) return;
    if (event.key === 'Escape') { swallow(); forced = false; suppressed = true; paint(); return; }
    if (event.key === 'Backspace' || event.key === 'Delete') { swallow(); enqueue('\b'); return; }
    if (event.key === 'Enter') { swallow(); if (!event.repeat) enqueue('\n'); return; }
    if (event.key.length === 1) {
      swallow();
      let ch = event.key;
      // some synthetic key sources report the unshifted key with shiftKey set
      if (event.shiftKey && /^[a-z]$/.test(ch) && !event.getModifierState('CapsLock')) ch = ch.toUpperCase();
      enqueue(ch); return;
    }
    // modifiers / arrows / function keys fall through untouched
  }, true);
  // keep the page's shortcut keyup handler from seeing keys we consumed
  document.addEventListener('keyup', event => {
    if (active() && (event.key.length === 1 || event.key === 'Escape') && !event.ctrlKey && !event.metaKey) event.stopImmediatePropagation();
  }, true);

  // ---- auto-detection of the on-screen keyboard -------------------------------------------------------
  setInterval(() => {
    if (!isReady()) return;
    const cur = look();
    visible = !!cur;
    if (cur) last = cur;
    if (!visible) suppressed = false;    // keyboard gone: re-arm auto typing for the next one
    paint();
  }, POLL_MS);
  paint();

  // debug / scripting handle: window.iphoneKeyboard.type('http://web.test:8090')
  window.iphoneKeyboard = {
    type: s => { for (const ch of String(s)) enqueue(ch === '\r' ? '\n' : ch); },
    state: () => ({active: active(), forced, suppressed, visible, layout: look(), shift: shiftLit(context), queued: queue.length, dropped}),
    tap,
  };
}
