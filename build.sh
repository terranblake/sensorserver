#!/bin/bash
# Set JAVA_HOME to Java 17 for build
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
./gradlew assembleDebug

# Then install the app with the system's adb
adb install -r ./app/build/outputs/apk/debug/app-debug.apk 