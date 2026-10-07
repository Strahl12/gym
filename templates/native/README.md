# Native-app template overlay

Pages here are served ONLY to the iOS app (user-agent contains
"GymCoachNative"); every other page falls back to the shared template in
templates/. To fork a page for the app without touching the web app:

    cp templates/workout.html templates/native/workout.html

…then edit the copy. Delete the copy to re-converge. See chat_server._render.
