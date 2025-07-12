<h2 style="color: #2978d7;">Reflection Report</h2>

Throughout this project, my primary goal was to build a robust dialogue system with real-time speech-to-speech interaction, using Pygame, OpenGL, and the OpenAI Realtime API. This required me to not only implement new features but also adapt to evolving technologies and integrate code from various sources.

<h3 style="color: #2978d7;">Initial Approach and Planning</h3>
At the outset, I reviewed the project requirements and identified the main components: a 3D office environment, interactive NPCs, and seamless speech interaction. My initial plan was to set up the core Pygame/OpenGL environment and basic player/NPC interaction before integrating any advanced AI or audio features. This modular approach allowed me to test each part in isolation.

<h3 style="color: #2978d7;">Challenges and Adaptations</h3>
One of the biggest challenges was integrating real-time audio with the OpenAI Realtime API, especially since OpenAI’s official repositories and documentation are evolving and sometimes use different patterns or tools. I found that various OpenAI examples use different libraries, async patterns, and connection methods. Some code samples were for general web apps or command-line tools, not for a real-time game loop. I had to carefully study these differences and adapt the code to fit the structure and requirements of my project.

<h3 style="color: #2978d7;">Solving Technical Issues</h3>
To overcome these challenges, I combined several strategies:

- I experimented with official OpenAI example repos, testing their audio streaming and connection handling, then selectively adapted portions of their code into my own application.
- I refactored the event loop logic to run asyncio within a thread, allowing Pygame’s loop and the async OpenAI API to work together smoothly.

During this process, I also encountered technical hurdles with audio buffering, session handling, and concurrency, particularly when switching between text and speech modes. Addressing these required multiple rounds of testing and debugging.

<h3 style="color: #2978d7;">Learning and Growth</h3>
This project significantly deepened my understanding of:

- **Async programming in Python**: Integrating synchronous (Pygame) and asynchronous (OpenAI API) codebases.
- **OpenAI Realtime API**: Understanding different usage patterns, adapting them for real-time, interactive contexts.

<h3 style="color: #2978d7;">Conclusion</h3>
Overall, adapting code from different official OpenAI repositories was both challenging and rewarding. It required careful analysis, creativity, and persistence to fit external code into my project’s unique architecture. 