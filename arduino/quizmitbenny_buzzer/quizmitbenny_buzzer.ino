// quizmitbenny buzzer firmware – superset of musikquiz/arduino/buzzer_external_PSU.
//
// Fully compatible with musikquiz (it never sends the 'B' command, so behavior
// there is identical). New command for quizmitbenny:
//   "B <mask>"  – off-mask, bits 0..4. Buzzers in the mask fade from green to
//                 dark over OFF_FADE_MS and their presses are ignored.
//                 "B 0" clears it; "9" (all active) clears it too.
//   "5" (reset) does NOT clear the mask – it persists between questions.

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>

#define MAX_LED 14

Adafruit_NeoPixel strip0 = Adafruit_NeoPixel(MAX_LED, 2, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel strip1 = Adafruit_NeoPixel(MAX_LED, 3, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel strip2 = Adafruit_NeoPixel(MAX_LED, 4, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel strip3 = Adafruit_NeoPixel(MAX_LED, 5, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel strip4 = Adafruit_NeoPixel(MAX_LED, 6, NEO_GRB + NEO_KHZ800);

Adafruit_NeoPixel* strips[5] = { &strip0, &strip1, &strip2, &strip3, &strip4 };

const byte p0 = A1;
const byte p1 = A2;
const byte p2 = A3;
const byte p3 = A4;
const byte p4 = A5;
const byte l0 = 10;
const byte l1 = 11;
const byte l2 = 12;
const byte l3 = 13;
const byte l4 = 9;
const byte clearPin = A0;

struct Player {
  byte buzzer;
  byte led;
};

Player const player[] = {
  {p0, l0},
  {p1, l1},
  {p2, l2},
  {p3, l3},
  {p4, l4}
};

// --- State machine ---
enum class State {
  IDLE,
  BUZZED,
  WAIT_FOR_CLEAR
} state;

int8_t pressed = -1;
String buzzer_blocked = "5";
int clearPinState = 9;

// quizmitbenny: off-mask – buzzers fade to dark and can't buzz
uint8_t offMask = 0;
float offFade[5] = {1.0, 1.0, 1.0, 1.0, 1.0}; // 1.0 = normal, 0.0 = dark
#define OFF_FADE_MS 3000                       // green -> darkness in 3 s

// --- Timing ---
uint32_t now = 0;
uint32_t ignorePressUntil = 0; // guard window after hardware clear

// --- Idle phase (smooth sine-wave breathing) ---
// Each strip has its own phase offset so they don't all pulse in sync
float idlePhase[5]     = {0.0, 0.0, 0.0, 0.0, 0.0}; // all in sync
float idleSpeed        = 0.004;  // radians per ms  → ~1.57 sec per full cycle
uint32_t lastIdleUpdate = 0;
#define IDLE_UPDATE_MS 20         // update every 20 ms (50 fps)
#define IDLE_MIN_BRIGHT 2
#define IDLE_MAX_BRIGHT 180
#define START_STEP_DELAY_MS 250  // delay between strips on start sequence
#define RESET_FADEIN_MS 2000
#define RESET_FADEIN_STEP_MS 20
#define FEEDBACK_BLINKS 8
#define FEEDBACK_ON_MS 100
// Fixed off-time between blinks for a consistent feedback rhythm
#define FEEDBACK_OFF_MS 150

// --- Pause animation ---
bool pauseActive = false;
uint32_t pauseNextTick = 0;
uint32_t pauseModeEnd = 0;
int pauseMode = 0;
uint32_t pauseTickMs = 120; // per-mode tempo (randomized)
#define PAUSE_UPDATE_MS 120
#define PAUSE_MODE_MIN_MS 2000
#define PAUSE_MODE_MAX_MS 8000
#define PAUSE_MAX_BRIGHT 255

// Pause mode state (for chase/pingpong/stack/etc)
int chasePos = 0;
int chaseDir = 1;
int chaseTail = 1;
uint8_t chaseR = 255, chaseG = 255, chaseB = 255;
int pingPos = 0;
int pingDir = 1;
int stackStage = 0;
int stackIndex = 0;
int gradientOffset = 0;

// --- Fade-out after buzz (non-buzzed strips) ---
uint32_t fadeStartTime   = 0;
#define FADE_DURATION_MS 1000     // 1 second fade to black
float    fadeLevel[5]    = {1.0, 1.0, 1.0, 1.0, 1.0}; // 1.0 = full, 0.0 = off

// --- Blink (buzzed strip) ---
uint32_t lastBlinkTime   = 0;
bool     blinkOn         = false;
#define BLINK_INTERVAL_MS 80     // fast hard blink

// --- Helpers ---

// Returns true if strip i is currently blocked (red idle)
bool isBlocked(int i) {
  return (clearPinState == i);
}

// Returns true if strip i is switched off via the off-mask (quizmitbenny)
bool isOff(int i) {
  return (offMask >> i) & 1;
}

void setStripColor(int idx, uint8_t r, uint8_t g, uint8_t b) {
  for (int i = 0; i < MAX_LED; i++) {
    strips[idx]->setPixelColor(i, strips[idx]->Color(r, g, b));
  }
  strips[idx]->show();
}

void clearAllStrips() {
  for (int i = 0; i < 5; i++) {
    setStripColor(i, 0, 0, 0);
  }
}

void setStripIdleFull(int idx) {
  uint8_t fb = (uint8_t)(IDLE_MAX_BRIGHT * offFade[idx]);
  if (isBlocked(idx)) {
    setStripColor(idx, fb, 0, 0);
  } else {
    setStripColor(idx, 0, fb, 0);
  }
}

void playStartSequence() {
  pressed = -1;
  blinkOn = false;
  clearAllStrips();

  for (int i = 0; i < 5; i++) {
    setStripIdleFull(i);
    delay(START_STEP_DELAY_MS);
  }

  // Reset idle phases — all in sync
  for (int i = 0; i < 5; i++) {
    idlePhase[i] = HALF_PI;
    fadeLevel[i] = 1.0;
  }

  now = millis();
  lastIdleUpdate = now;
  state = State::IDLE;
}

void playStartSequenceTimed(uint32_t initial_ms, uint32_t step_ms) {
  pressed = -1;
  blinkOn = false;
  clearAllStrips();

  if (initial_ms > 0) delay(initial_ms);

  for (int i = 0; i < 5; i++) {
    setStripIdleFull(i);
    delay(step_ms);
  }

  // Reset idle phases — all in sync
  for (int i = 0; i < 5; i++) {
    idlePhase[i] = HALF_PI;
    fadeLevel[i] = 1.0;
  }

  now = millis();
  lastIdleUpdate = now;
  state = State::IDLE;
}

void startPauseMode() {
  pauseActive = true;
  pauseNextTick = millis();
  pauseMode = random(0, 4); // pick one of several modes (confetti & sparkle removed)
  pauseModeEnd = pauseNextTick + random(PAUSE_MODE_MIN_MS, PAUSE_MODE_MAX_MS + 1);

  // init mode-specific state
  chasePos = random(0, 5);
  chaseDir = (random(0, 2) == 0) ? -1 : 1;
  chaseTail = random(1, 4);
  chaseR = random(0, PAUSE_MAX_BRIGHT + 1);
  chaseG = random(0, PAUSE_MAX_BRIGHT + 1);
  chaseB = random(0, PAUSE_MAX_BRIGHT + 1);

  pingPos = random(0, 5);
  pingDir = (random(0, 2) == 0) ? -1 : 1;
  stackStage = 0;
  stackIndex = 0;
  gradientOffset = random(0, 360);
}

void playPauseTick() {
  uint32_t tnow = millis();
  if (tnow < pauseNextTick) return;
  pauseNextTick = tnow + pauseTickMs;

  // If mode expired, pick a new mode and randomize params and tempo
  if (tnow >= pauseModeEnd) {
    pauseMode = random(0, 4);
    pauseModeEnd = tnow + random(PAUSE_MODE_MIN_MS, PAUSE_MODE_MAX_MS + 1);

    // pick a tempo range depending on mode (ms)
    switch (pauseMode) {
      case 0: // chase
        pauseTickMs = random(80, 250);
        break;
      case 1: // ping-pong
        pauseTickMs = random(100, 350);
        break;
      case 2: // stack
        pauseTickMs = random(150, 450);
        break;
      case 3: // gradient
        pauseTickMs = random(80, 300);
        break;
      default:
        pauseTickMs = random(60, 300);
    }

    // re-init mode params lightly
    chasePos = random(0, 5);
    chaseDir = (random(0, 2) == 0) ? -1 : 1;
    chaseTail = random(1, 4);
    chaseR = random(0, PAUSE_MAX_BRIGHT + 1);
    chaseG = random(0, PAUSE_MAX_BRIGHT + 1);
    chaseB = random(0, PAUSE_MAX_BRIGHT + 1);
    pingPos = random(0, 5);
    pingDir = (random(0, 2) == 0) ? -1 : 1;
    stackStage = 0;
    stackIndex = 0;
    gradientOffset = random(0, 360);
  }

  // occasional tiny tempo jitter while running (5% chance)
  if (random(100) < 5) {
    pauseTickMs = max(20UL, min(800UL, (uint32_t)(pauseTickMs * (90 + random(21)) / 100)));
  }

  // Simple per-mode updates
  switch (pauseMode) {
    case 0: // chase with tail
      clearAllStrips();
      for (int t = 0; t <= chaseTail; t++) {
        int idx = (chasePos - t * chaseDir + 500) % 5;
        uint8_t r = (uint8_t)max(0, (int)chaseR - t * 40);
        uint8_t g = (uint8_t)max(0, (int)chaseG - t * 40);
        uint8_t b = (uint8_t)max(0, (int)chaseB - t * 40);
        setStripColor(idx, r, g, b);
      }
      chasePos = (chasePos + chaseDir + 5) % 5;
      break;

    case 1: // ping-pong
      clearAllStrips();
      setStripColor(pingPos, random(256), random(256), random(256));
      pingPos += pingDir;
      if (pingPos < 0) { pingPos = 1; pingDir = 1; }
      if (pingPos > 4) { pingPos = 3; pingDir = -1; }
      break;

    case 2: // stack/back-from-5th style
      clearAllStrips();
      // light from right (5) to left progressively
      for (int i = 4; i >= max(0, 4 - stackIndex); i--) {
        setStripColor(i, random(256), random(256), random(256));
      }
      stackIndex++;
      if (stackIndex > 4) stackIndex = 0;
      break;

    case 3: // gradient sweep
      for (int i = 0; i < 5; i++) {
        int hue = (gradientOffset + i * 36) % 360;
        // simple hue->rgb approximate via wheel
        uint8_t r, g, b;
        int h = hue / 60;
        int f = hue % 60;
        if (h == 0) { r = 255; g = (f * 255) / 60; b = 0; }
        else if (h == 1) { r = (255 - (f * 255) / 60); g = 255; b = 0; }
        else if (h == 2) { r = 0; g = 255; b = (f * 255) / 60; }
        else if (h == 3) { r = 0; g = (255 - (f * 255) / 60); b = 255; }
        else if (h == 4) { r = (f * 255) / 60; g = 0; b = 255; }
        else { r = 255; g = 0; b = (255 - (f * 255) / 60); }
        setStripColor(i, r, g, b);
      }
      gradientOffset = (gradientOffset + 10) % 360;
      break;
  }
}

void playResetFadeIn() {
  uint32_t start = millis();
  uint32_t elapsed = 0;

  while (elapsed < RESET_FADEIN_MS) {
    float t = (float)elapsed / (float)RESET_FADEIN_MS; // 0..1

    for (int i = 0; i < 5; i++) {
      uint8_t bright = (uint8_t)(t * IDLE_MAX_BRIGHT * offFade[i]);
      if (isBlocked(i)) {
        setStripColor(i, bright, 0, 0);
      } else {
        setStripColor(i, 0, bright, 0);
      }
    }

    delay(RESET_FADEIN_STEP_MS);
    elapsed = millis() - start;
  }

  // Ensure full brightness at the end of the fade
  for (int i = 0; i < 5; i++) {
    setStripIdleFull(i);
  }
}

void playAnswerFeedback(bool correct) {
  uint8_t r = correct ? 0 : IDLE_MAX_BRIGHT;
  uint8_t g = correct ? IDLE_MAX_BRIGHT : 0;

  clearAllStrips();

  if (pressed >= 0 && pressed < 5) {
    // Wheel-of-fortune style slowing: start fast and gradually increase the pause
    for (int i = 0; i < FEEDBACK_BLINKS; i++) {
      // short on pulse
      setStripColor(pressed, r, g, 0);
      delay(FEEDBACK_ON_MS);
      // turn off
      setStripColor(pressed, 0, 0, 0);

      // fixed off-time between blinks for consistent rhythm
      delay(FEEDBACK_OFF_MS);
    }
  }

  pressed = -1;
  blinkOn = false;

  // Reset idle phases — all in sync
  for (int i = 0; i < 5; i++) {
    idlePhase[i] = HALF_PI;
    fadeLevel[i] = 1.0;
  }

  playResetFadeIn();

  now = millis();
  lastIdleUpdate = now;
  state = State::IDLE;
}

void read_blocked_buzzer() {
  if (Serial.available()) {
    buzzer_blocked = Serial.readStringUntil('\n');
    buzzer_blocked.trim();
    if (buzzer_blocked.length() == 0) return;

    // Answer feedback commands (G/R)
    if (buzzer_blocked == "G" || buzzer_blocked == "R") {
      playAnswerFeedback(buzzer_blocked == "G");
      return;
    }

    // Pause mode
    if (buzzer_blocked == "P") {
      // Start pause mode
      startPauseMode();
      return;
    }

    // Buzzer off-mask (quizmitbenny): "B <mask>", bits 0..4
    if (buzzer_blocked.charAt(0) == 'B') {
      char bbuf[32];
      buzzer_blocked.toCharArray(bbuf, sizeof(bbuf));
      unsigned long m = 0;
      if (sscanf(bbuf, "B %lu", &m) == 1) {
        offMask = (uint8_t)(m & 0b11111);
      }
      return;
    }

    // Start sequence: allow "S" or "S <initial_ms> <step_ms>"
    if (buzzer_blocked.charAt(0) == 'S') {
      // Stop pause mode if active
      pauseActive = false;

      // Robust parse using C sscanf on a char buffer
      char buf[64];
      buzzer_blocked.toCharArray(buf, sizeof(buf));
      unsigned long initial_ms = 0;
      unsigned long step_ms = 0;
      int parsed = sscanf(buf, "S %lu %lu", &initial_ms, &step_ms);

      if (parsed == 2) {
        playStartSequenceTimed(initial_ms, step_ms);
      } else if (parsed == 1) {
        playStartSequenceTimed(initial_ms, START_STEP_DELAY_MS);
      } else {
        playStartSequence();
      }
      return;
    }

    // Tempo update command: 'M <tick_ms>' -> set pauseTickMs immediately
    if (buzzer_blocked.charAt(0) == 'M') {
      char mbuf[32];
      buzzer_blocked.toCharArray(mbuf, sizeof(mbuf));
      unsigned long new_tick = 0;
      int parsed_m = sscanf(mbuf, "M %lu", &new_tick);
      if (parsed_m == 1) {
        if (new_tick < 30) new_tick = 30;
        if (new_tick > 800) new_tick = 800;
        pauseTickMs = (uint32_t)new_tick;
        pauseNextTick = millis(); // apply immediately
      }
      return;
    }

    // Numeric blocked-value message (0..9 or similar)
    bool isNumeric = true;
    for (unsigned int i = 0; i < buzzer_blocked.length(); i++) {
      if (!isDigit(buzzer_blocked.charAt(i))) {
        isNumeric = false;
        break;
      }
    }

    if (isNumeric) {
      clearPinState = buzzer_blocked.toInt();
      if (clearPinState == 9) offMask = 0; // "all active" also lifts the off-mask
    }
  }
}

// --- Setup ---
void setup() {
  Serial.begin(9600);
  Serial.setTimeout(200);
  // Seed RNG
  randomSeed(analogRead(A0) ^ micros());

  for (int i = 0; i < 5; i++) {
    strips[i]->begin();
    strips[i]->show();
  }

  pinMode(clearPin, INPUT_PULLUP);
  for (auto &p : player) {
    pinMode(p.buzzer, INPUT_PULLUP);
    pinMode(p.led, OUTPUT);
  }

  state = State::IDLE;
}

// --- Loop ---
void loop() {
  now = millis();
  read_blocked_buzzer();

  if (pauseActive) {
    playPauseTick();
    return;
  }

  switch (state) {

    // -------------------------------------------------------
    case State::IDLE: {
      // Update breathing every IDLE_UPDATE_MS
      if (now - lastIdleUpdate >= IDLE_UPDATE_MS) {
        lastIdleUpdate = now;

        float fadeStep = (float)IDLE_UPDATE_MS / OFF_FADE_MS;
        for (int i = 0; i < 5; i++) {
          idlePhase[i] += idleSpeed * IDLE_UPDATE_MS;
          if (idlePhase[i] > TWO_PI) idlePhase[i] -= TWO_PI;

          // ramp toward on/off target: 0 -> dark over OFF_FADE_MS (3 s)
          float target = isOff(i) ? 0.0f : 1.0f;
          if (offFade[i] < target) offFade[i] += fadeStep;
          else if (offFade[i] > target) offFade[i] -= fadeStep;
          if (offFade[i] < 0.0f) offFade[i] = 0.0f;
          if (offFade[i] > 1.0f) offFade[i] = 1.0f;

          // sine goes -1..+1, remap to IDLE_MIN..IDLE_MAX, scaled by offFade
          float s = (sin(idlePhase[i]) + 1.0) * 0.5; // 0.0 .. 1.0
          uint8_t bright = (uint8_t)(
              (IDLE_MIN_BRIGHT + s * (IDLE_MAX_BRIGHT - IDLE_MIN_BRIGHT))
              * offFade[i]);

          if (isBlocked(i)) {
            setStripColor(i, bright, 0, 0); // blocked → red breathing
          } else {
            setStripColor(i, 0, bright, 0); // normal  → green breathing
          }
        }
      }

      // Check for buzzer press
      if (now < ignorePressUntil) {
        break;
      }
      for (byte i = 0; i < 5; i++) {
        if (digitalRead(player[i].buzzer) == LOW) {
          if (clearPinState != i && !isOff(i)) {
            pressed = i;
            Serial.println(i);

            // Capture current brightness of each strip for fade-out start
            fadeStartTime = now;
            for (int j = 0; j < 5; j++) {
              if (j != pressed) {
                // Start from the current off-fade level (off-masked stay dark)
                fadeLevel[j] = offFade[j];
                uint8_t fb = (uint8_t)(IDLE_MAX_BRIGHT * offFade[j]);
                if (isBlocked(j)) {
                  setStripColor(j, fb, 0, 0);
                } else {
                  setStripColor(j, 0, fb, 0);
                }
              }
            }

            // Immediately set the pressed strip to blue (start blink)
            blinkOn = true;
            lastBlinkTime = now;
            setStripColor(pressed, 0, 0, 255);

            state = State::BUZZED;
            break;
          }
        }
      }
      break;
    }

    // -------------------------------------------------------
    case State::BUZZED: {
      uint32_t elapsed = now - fadeStartTime;

      // Fade out non-buzzed strips
      for (int i = 0; i < 5; i++) {
        if (i == pressed) continue;

        float progress = (float)elapsed / FADE_DURATION_MS; // 0..1
        if (progress >= 1.0) progress = 1.0;

        // Start from the brightness captured at buzz moment
        float level = fadeLevel[i] * (1.0 - progress);
        uint8_t bright = (uint8_t)(level * IDLE_MAX_BRIGHT);

        if (isBlocked(i)) {
          setStripColor(i, bright, 0, 0);
        } else {
          setStripColor(i, 0, bright, 0);
        }
      }

      // Hard fast blue blink on buzzed strip
      if (now - lastBlinkTime >= BLINK_INTERVAL_MS) {
        lastBlinkTime = now;
        blinkOn = !blinkOn;
        if (blinkOn) {
          setStripColor(pressed, 0, 0, 255);
        } else {
          setStripColor(pressed, 0, 0, 0);
        }
      }

      // After fade is complete, move to WAIT_FOR_CLEAR
      if (elapsed >= FADE_DURATION_MS) {
        // Make sure non-buzzed strips are fully off
        for (int i = 0; i < 5; i++) {
          if (i != pressed) setStripColor(i, 0, 0, 0);
        }
        state = State::WAIT_FOR_CLEAR;
      }
      break;
    }

    // -------------------------------------------------------
    case State::WAIT_FOR_CLEAR: {
      // Buzzed strip keeps hard-blinking blue
      if (now - lastBlinkTime >= BLINK_INTERVAL_MS) {
        lastBlinkTime = now;
        blinkOn = !blinkOn;
        if (blinkOn) {
          setStripColor(pressed, 0, 0, 255);
        } else {
          setStripColor(pressed, 0, 0, 0);
        }
      }

      // Wait for clear signal (hardware button or Python sending "5")
      if (digitalRead(clearPin) == LOW) {
        // Hardware clear: debounce and wait for release
        delay(20);
        if (digitalRead(clearPin) == LOW) {
          while (digitalRead(clearPin) == LOW) {
            delay(5);
          }

          clearAllStrips();
          pressed = -1;
          clearPinState = 9; // reset to default (no blocked)
          offMask = 0;

          // Stop pause if active
          pauseActive = false;

          // Reset idle phases — all in sync
          for (int i = 0; i < 5; i++) {
            idlePhase[i] = HALF_PI;
            fadeLevel[i] = 1.0;
          }

          playResetFadeIn();

          now = millis();
          lastIdleUpdate = now;
          ignorePressUntil = now + 200; // guard window after hardware clear
          state = State::IDLE;
        }
      } else if (clearPinState == 5) {
        // Python app clear path (unchanged behavior)
        clearAllStrips();
        pressed = -1;
        clearPinState = 5;

        // Stop pause if active
        pauseActive = false;

        // Reset idle phases — all in sync
        for (int i = 0; i < 5; i++) {
          idlePhase[i] = HALF_PI;
          fadeLevel[i] = 1.0;
        }

        playResetFadeIn();

        lastIdleUpdate = millis();
        state = State::IDLE;
      }
      break;
    }
  }
}
