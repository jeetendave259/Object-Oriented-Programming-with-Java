# Java Learning Hub

A browser-based study resource for Object-Oriented Programming with Java (OOPJ). The project includes a dashboard, five unit study guides, interactive examples, and a local server for running Java code from the guides.

## Contents

| File | Topics |
| --- | --- |
| `index.html` | Main dashboard with links to all units and a course progress tracker |
| `Unit1_BasicsOfJava.html` | Java fundamentals, types, operators, control flow, arrays, strings, command-line arguments, and wrapper classes |
| `Unit2ClassesMethods.html` | Object-oriented programming, classes, objects, encapsulation, methods, constructors, access modifiers, and related concepts |
| `Unit3InheritanceInterface.html` | Inheritance, polymorphism, abstract classes, interfaces, nested classes, packages, and class paths |
| `Unit4JAVAFXUI.html` | JavaFX application structure, shapes, layouts, event handling, and animation |
| `Unit5ClassModeling.html` | UML, class modeling, associations, Java I/O, exception handling, and multithreading |
| `run_java_server.py` | Local web server and Java/JavaFX example runner |

The unit pages provide navigation back to the dashboard. Completion checkboxes on the dashboard save progress in the current browser's local storage.

## Requirements

- Python 3
- A Java Development Kit (JDK) with `java` and `javac` available on `PATH`
- A modern browser
- JavaFX libraries for the JavaFX examples in Unit 4; set `JAVAFX_LIB` to the JavaFX `lib` directory if the libraries are not found in a standard location

## Run the project

From this directory, start the local server:

```bash
python3 run_java_server.py
```

Open the dashboard in your browser:

```text
http://127.0.0.1:8000/
```

You can also open a specific unit directly, for example:

```text
http://127.0.0.1:8000/Unit1_BasicsOfJava.html
```

Keep the server running while using runnable code examples. The runner compiles and executes Java examples locally. JavaFX examples launch as desktop applications and require a working JavaFX installation.

Stop the server with **Ctrl+C** in the terminal where it is running.

## Use without the local runner

The study guides can be opened directly in a browser from `index.html`. Navigation and reading work without starting the server, but runnable examples that use the local execution APIs require the server.

## Notes

- The server listens only on `127.0.0.1:8000`.
- Dashboard completion progress is stored by the browser and is not shared across browsers or devices.
- No package installation is required to use the project.
