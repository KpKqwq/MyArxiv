/* MyArxiv paper list: only one small JSON page is rendered at a time. */

function renderPaperMath(container) {
    if (typeof renderMathInElement !== "function") return;
    renderMathInElement(container, {
        delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
            { left: "\\(", right: "\\)", display: false },
            { left: "\\[", right: "\\]", display: true }
        ],
        throwOnError: false
    });
}

function createPaper(paper) {
    const article = document.createElement("article");
    const details = document.createElement("details");
    details.className = "article-expander";

    const summary = document.createElement("summary");
    summary.className = "article-expander-title";
    summary.innerHTML = `${paper.revised ? "♻ " : ""}${paper.title_html} ${paper.conference_html}`;
    details.appendChild(summary);

    const authors = document.createElement("div");
    authors.className = "article-authors";
    authors.innerHTML =
        `<a href="${paper.id}" target="_blank" rel="noopener noreferrer"><i class="ri-links-line"></i></a>` +
        `<a href="${paper.pdf_url}" target="_blank" rel="noopener noreferrer"><i class="ri-file-paper-2-line"></i></a>` +
        paper.authors_html;
    details.appendChild(authors);

    const content = document.createElement("div");
    content.className = "article-lazy-content";
    details.appendChild(content);

    details.addEventListener("toggle", () => {
        if (!details.open || details.dataset.loaded === "true") return;

        const summaryBox = document.createElement("div");
        summaryBox.className = "article-summary-box-inner";
        summaryBox.textContent = paper.summary;
        content.appendChild(summaryBox);

        if (paper.comment) {
            const commentBox = document.createElement("div");
            commentBox.className = "article-summary-box-inner";
            const chip = document.createElement("span");
            chip.className = "chip";
            chip.textContent = "comment";
            commentBox.append(chip, document.createTextNode(`: ${paper.comment}`));
            content.appendChild(commentBox);
        }

        renderPaperMath(content);
        details.dataset.loaded = "true";
    });

    article.appendChild(details);
    return article;
}

function setupSubjectLoader(details) {
    let pages;
    try {
        pages = JSON.parse(details.dataset.pages || "[]");
    } catch (error) {
        console.error("[MyArxiv] Invalid paper page metadata", error);
        return;
    }

    const list = details.querySelector(".paper-list");
    const sentinel = details.querySelector(".paper-load-sentinel");
    let nextPage = 0;
    let loading = false;
    let observer;

    async function loadNextPage() {
        if (loading || nextPage >= pages.length) return;
        loading = true;
        try {
            const response = await fetch(pages[nextPage].url, { cache: "force-cache" });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const papers = await response.json();
            const fragment = document.createDocumentFragment();
            for (const paper of papers) fragment.appendChild(createPaper(paper));
            list.appendChild(fragment);
            nextPage += 1;
            if (nextPage >= pages.length && observer) {
                observer.disconnect();
                sentinel.remove();
            }
        } catch (error) {
            console.error("[MyArxiv] Failed to load paper page", error);
            sentinel.textContent = "Failed to load papers. Open the group again to retry.";
        } finally {
            loading = false;
        }
    }

    details.addEventListener("toggle", () => {
        if (details.open) void loadNextPage();
    });

    observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting) && details.open) void loadNextPage();
    }, { rootMargin: "800px 0px" });
    observer.observe(sentinel);
}

function setupLazyPapers() {
    document.querySelectorAll(".subject-expander").forEach(setupSubjectLoader);
}

document.addEventListener("DOMContentLoaded", setupLazyPapers);

let subjectsExpanded = false;
document.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const activeTag = document.activeElement ? document.activeElement.tagName : "";
    if (["INPUT", "TEXTAREA", "BUTTON", "SELECT"].includes(activeTag)) return;
    event.preventDefault();
    subjectsExpanded = !subjectsExpanded;
    document.querySelectorAll(".subject-expander").forEach(details => {
        details.open = subjectsExpanded;
    });
});

const toggleSwitch = document.querySelector('.theme-switch input[type="checkbox"]');
function switchTheme(event) {
    const theme = event.target.checked ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
    document.getElementById("theme-icon").className =
        theme === "light" ? "ri-sun-line" : "ri-moon-line";
    localStorage.setItem("theme", theme);
}

if (toggleSwitch) toggleSwitch.addEventListener("change", switchTheme, false);
const currentTheme = localStorage.getItem("theme");
if (currentTheme) {
    document.documentElement.setAttribute("data-theme", currentTheme);
    if (currentTheme === "light" && toggleSwitch) {
        toggleSwitch.checked = true;
        document.getElementById("theme-icon").className = "ri-sun-line";
    }
}
