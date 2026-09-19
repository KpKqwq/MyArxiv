/*
 * MyArxiv front-end
 *
 * 1. Abstract/comment are loaded from cache.json only when a paper is opened.
 * 2. cache.json is fetched only once and then kept in memory.
 * 3. KaTeX renders only the currently opened paper.
 * 4. TAB expands/collapses subject groups only.
 */

let paperMapPromise = null;

function getPaperMap() {
    if (paperMapPromise) {
        return paperMapPromise;
    }

    paperMapPromise = fetch("./cache.json", {
        cache: "force-cache"
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(
                    `Failed to load cache.json: HTTP ${response.status}`
                );
            }

            return response.json();
        })
        .then(data => {
            if (!Array.isArray(data)) {
                throw new Error(
                    "Unexpected cache.json format: expected an array."
                );
            }

            const map = new Map();

            for (const paper of data) {
                if (paper && typeof paper.id === "string") {
                    map.set(paper.id, paper);
                }
            }

            console.log(
                `[MyArxiv] ${map.size} papers loaded from cache.json`
            );

            return map;
        })
        .catch(error => {
            paperMapPromise = null;
            throw error;
        });

    return paperMapPromise;
}

function renderPaperMath(container) {
    if (typeof renderMathInElement !== "function") {
        return;
    }

    renderMathInElement(container, {
        delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
            { left: "\\(", right: "\\)", display: false },
            { left: "\\[", right: "\\]", display: true },
            {
                left: "\\begin{equation}",
                right: "\\end{equation}",
                display: true
            },
            {
                left: "\\begin{align}",
                right: "\\end{align}",
                display: true
            },
            {
                left: "\\begin{alignat}",
                right: "\\end{alignat}",
                display: true
            },
            {
                left: "\\begin{gather}",
                right: "\\end{gather}",
                display: true
            },
            {
                left: "\\begin{CD}",
                right: "\\end{CD}",
                display: true
            }
        ],
        throwOnError: false
    });
}

function renderPaperContent(container, paper) {
    container.replaceChildren();

    if (paper.summary) {
        const summaryBox = document.createElement("div");
        summaryBox.className = "article-summary-box-inner";

        const summaryText = document.createElement("span");
        summaryText.textContent = paper.summary;

        summaryBox.appendChild(summaryText);
        container.appendChild(summaryBox);
    }

    if (paper.comment) {
        const commentBox = document.createElement("div");
        commentBox.className = "article-summary-box-inner";

        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = "comment";

        commentBox.appendChild(chip);
        commentBox.appendChild(
            document.createTextNode(": " + paper.comment)
        );

        container.appendChild(commentBox);
    }

    renderPaperMath(container);
}

function setupLazyPapers() {
    document
        .querySelectorAll(".article-expander")
        .forEach(details => {
            details.addEventListener("toggle", async () => {
                if (!details.open) {
                    return;
                }

                if (details.dataset.loaded === "true") {
                    return;
                }

                if (details.dataset.loading === "true") {
                    return;
                }

                const container = details.querySelector(
                    ".article-lazy-content"
                );

                if (!container) {
                    return;
                }

                details.dataset.loading = "true";
                container.textContent = "Loading...";

                try {
                    const paperMap = await getPaperMap();
                    const paperId = details.dataset.paperId;
                    const paper = paperMap.get(paperId);

                    if (!paper) {
                        throw new Error(
                            `Paper not found in cache.json: ${paperId}`
                        );
                    }

                    renderPaperContent(container, paper);
                    details.dataset.loaded = "true";

                } catch (error) {
                    console.error("[MyArxiv]", error);
                    container.textContent =
                        "Failed to load paper details.";

                } finally {
                    details.dataset.loading = "false";
                }
            });
        });
}

document.addEventListener(
    "DOMContentLoaded",
    setupLazyPapers
);


/* Expand / Collapse SUBJECT groups with TAB */
let subjectsExpanded = false;

document.addEventListener("keydown", function (event) {
    if (event.key !== "Tab") {
        return;
    }

    const activeTag = document.activeElement
        ? document.activeElement.tagName
        : "";

    if (
        activeTag === "INPUT" ||
        activeTag === "TEXTAREA" ||
        activeTag === "BUTTON" ||
        activeTag === "SELECT"
    ) {
        return;
    }

    event.preventDefault();

    subjectsExpanded = !subjectsExpanded;

    document
        .querySelectorAll(".subject-expander")
        .forEach(details => {
            details.open = subjectsExpanded;
        });
});


/* Switch Theme */
const toggleSwitch = document.querySelector(
    '.theme-switch input[type="checkbox"]'
);

function switchTheme(e) {
    if (e.target.checked) {
        document.documentElement.setAttribute(
            "data-theme",
            "light"
        );
        document.getElementById(
            "theme-icon"
        ).className = "ri-sun-line";
        localStorage.setItem("theme", "light");
    } else {
        document.documentElement.setAttribute(
            "data-theme",
            "dark"
        );
        document.getElementById(
            "theme-icon"
        ).className = "ri-moon-line";
        localStorage.setItem("theme", "dark");
    }
}

if (toggleSwitch) {
    toggleSwitch.addEventListener(
        "change",
        switchTheme,
        false
    );
}

const currentTheme = localStorage.getItem("theme");

if (currentTheme) {
    document.documentElement.setAttribute(
        "data-theme",
        currentTheme
    );

    if (
        currentTheme === "light" &&
        toggleSwitch
    ) {
        toggleSwitch.checked = true;
    }
}


/* Build timestamp */
const timestamp = document.getElementById(
    "build-timestamp"
);

if (timestamp) {
    const timestampLocal = new Date(
        timestamp.getAttribute("datetime")
    ).toLocaleString();

    const badge = document.getElementById(
        "build-timestamp-badge"
    );

    void timestampLocal;
    void badge;
}
