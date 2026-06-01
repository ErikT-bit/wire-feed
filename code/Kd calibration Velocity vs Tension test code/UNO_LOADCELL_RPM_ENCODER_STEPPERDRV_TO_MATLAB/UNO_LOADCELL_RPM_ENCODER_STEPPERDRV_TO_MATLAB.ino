#include "HX711.h"
#include <AccelStepper.h>

// ---------------- PIN ASSIGNMENTS ----------------
const byte ENC_A_PIN   = 2;   // Brown
const byte ENC_B_PIN   = 3;   // Red

const byte HX_DT_PIN   = 5;   // moved off D2/D3
const byte HX_SCK_PIN  = 6;

const byte STEP_PIN    = 8;
const byte DIR_PIN     = 9;
const byte EN_PIN      = 10;  // optional

// ---------------- USER SETTINGS ----------------
// DM556 microstep switch setting must match this:
const long MOTOR_FULL_STEPS_PER_REV = 200;
const long MICROSTEPS = 16;                 // change to match DM556
const long MOTOR_STEPS_PER_REV = MOTOR_FULL_STEPS_PER_REV * MICROSTEPS;

// Your encoder is 500 PPR; with x4 quadrature decoding = 2000 counts/rev
const long ENCODER_COUNTS_PER_REV = 2000;   // change if your measured value says otherwise

// HX711 calibration
float calibration_factor = -7050.0;         // replace with your real calibration value
float tension_divisor = 2.0;                // pulley-on-load-cell assumption; actual tension = load / 2

// RPM controller gains
float Kp = 0.35f;
float Ki = 0.80f;

// Limits
float targetRPM = 0.0f;
float commandedRPM = 0.0f;
const float maxRPM = 250.0f;                // set for your system
const float minRPM = -250.0f;

HX711 scale;
AccelStepper motor(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

// ---------------- ENCODER STATE ----------------
volatile long encoderCount = 0;

void isrEncA() {
  bool a = digitalRead(ENC_A_PIN);
  bool b = digitalRead(ENC_B_PIN);
  if (a == b) encoderCount++;
  else encoderCount--;
}

void isrEncB() {
  bool a = digitalRead(ENC_A_PIN);
  bool b = digitalRead(ENC_B_PIN);
  if (a != b) encoderCount++;
  else encoderCount--;
}

// ---------------- SERIAL COMMAND BUFFER ----------------
char cmdBuf[64];
byte cmdIdx = 0;

// ---------------- RUNTIME STATE ----------------
unsigned long lastControlMs = 0;
unsigned long lastReportMs  = 0;
unsigned long lastLoadMs    = 0;

long lastEncoderCount = 0;
float measuredRPM = 0.0f;
float load_g = 0.0f;
float tension_g = 0.0f;
float tension_N = 0.0f;
float integrator = 0.0f;

void setMotorRPM(float rpm) {
  rpm = constrain(rpm, minRPM, maxRPM);
  commandedRPM = rpm;

  float stepsPerSec = rpm * MOTOR_STEPS_PER_REV / 60.0f;
  motor.setSpeed(stepsPerSec);
}

void processCommand(const char* cmd) {
  if (strncmp(cmd, "RPM=", 4) == 0) {
    targetRPM = atof(cmd + 4);
  }
  else if (strcmp(cmd, "STOP") == 0) {
    targetRPM = 0.0f;
    integrator = 0.0f;
  }
  else if (strcmp(cmd, "TARE") == 0) {
    scale.tare(10);
  }
  else if (strncmp(cmd, "CAL=", 4) == 0) {
    calibration_factor = atof(cmd + 4);
    scale.set_scale(calibration_factor);
  }
  else if (strncmp(cmd, "KP=", 3) == 0) {
    Kp = atof(cmd + 3);
  }
  else if (strncmp(cmd, "KI=", 3) == 0) {
    Ki = atof(cmd + 3);
  }
  else if (strncmp(cmd, "TDIV=", 5) == 0) {
    tension_divisor = atof(cmd + 5);
    if (tension_divisor == 0.0f) tension_divisor = 2.0f;
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (cmdIdx > 0) {
        cmdBuf[cmdIdx] = '\0';
        processCommand(cmdBuf);
        cmdIdx = 0;
      }
    } else {
      if (cmdIdx < sizeof(cmdBuf) - 1) {
        cmdBuf[cmdIdx++] = c;
      }
    }
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(ENC_A_PIN, INPUT_PULLUP);
  pinMode(ENC_B_PIN, INPUT_PULLUP);

  pinMode(EN_PIN, OUTPUT);
  digitalWrite(EN_PIN, LOW);   // may need HIGH depending on driver wiring

  attachInterrupt(digitalPinToInterrupt(ENC_A_PIN), isrEncA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B_PIN), isrEncB, CHANGE);

  scale.begin(HX_DT_PIN, HX_SCK_PIN);
  scale.set_scale(calibration_factor);
  scale.tare(10);

  motor.setMaxSpeed((maxRPM * MOTOR_STEPS_PER_REV / 60.0f) * 1.2f);
  motor.setSpeed(0.0f);

  Serial.println("time_ms,target_rpm,meas_rpm,cmd_rpm,load_g,tension_g,tension_N,enc_count");
}

void loop() {
  motor.runSpeed();
  readSerialCommands();

  unsigned long now = millis();

  // ---------- RPM control update ----------
  if (now - lastControlMs >= 50) {
    float dt = (now - lastControlMs) / 1000.0f;
    lastControlMs = now;

    noInterrupts();
    long countNow = encoderCount;
    interrupts();

    long dCount = countNow - lastEncoderCount;
    lastEncoderCount = countNow;

    if (dt > 0.0f) {
      measuredRPM = (dCount / (float)ENCODER_COUNTS_PER_REV) * (60.0f / dt);
    }

    float err = targetRPM - measuredRPM;
    integrator += err * dt;

    // anti-windup
    integrator = constrain(integrator, -100.0f, 100.0f);

    float rpmCorrection = Kp * err + Ki * integrator;
    float rpmToCommand = targetRPM + rpmCorrection;

    // if target is zero, fully stop
    if (fabs(targetRPM) < 0.01f) {
      integrator = 0.0f;
      rpmToCommand = 0.0f;
    }

    setMotorRPM(rpmToCommand);
  }

  // ---------- load cell update ----------
  if (now - lastLoadMs >= 100) {
    lastLoadMs = now;

    if (scale.is_ready()) {
      load_g = scale.get_units(1);   // faster update, less averaging
      tension_g = load_g / tension_divisor;
      tension_N = tension_g * 0.00980665f;
    }
  }

  // ---------- serial report ----------
  if (now - lastReportMs >= 100) {
    lastReportMs = now;

    noInterrupts();
    long countCopy = encoderCount;
    interrupts();

    Serial.print(now);
    Serial.print(",");
    Serial.print(targetRPM, 3);
    Serial.print(",");
    Serial.print(measuredRPM, 3);
    Serial.print(",");
    Serial.print(commandedRPM, 3);
    Serial.print(",");
    Serial.print(load_g, 3);
    Serial.print(",");
    Serial.print(tension_g, 3);
    Serial.print(",");
    Serial.print(tension_N, 4);
    Serial.print(",");
    Serial.println(countCopy);
  }
}