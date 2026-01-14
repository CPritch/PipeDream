# PipeDream

PipeDream will be a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

## Mission

Text adventures offer infinite resolution for the imagination, but modern generative models allow us to see those worlds. PipeDream bridges this gap by acting as a universal adapter between legacy text engines and modern image generation APIs.

## Core Goals

1. **Universal Compatibility**
   If a game runs in a terminal and outputs text to stdout, PipeDream should be able to visualize it. This includes Z-Machine interpreters (Frotz), Python text adventures, or custom binaries.

2. **Real-Time Responsiveness**
   The visualization process must be fast enough to keep pace with the gameplay loop. The architecture prioritizes low latency to maintain immersion.

3. **Visual Consistency**
   The tool aims to maintain narrative coherence. If a player drops an item or revisits a room, the generated imagery should reflect the previous state rather than hallucinating a completely new environment.

## Current Status

**Status: Scaffold / Concept**

This project is currently in the initial scaffolding phase. The architecture is being defined to support modular input streams (game engines) and modular output drivers (image generation APIs).