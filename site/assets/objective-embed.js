(() => {
  const frame = document.querySelector("[data-objective-embed]");
  if (!frame) return;

  const configUrl = frame.dataset.objectiveEmbed;
  fetch(configUrl, { credentials: "same-origin" })
    .then((response) => {
      if (!response.ok) throw new Error(`embed configuration returned ${response.status}`);
      return response.json();
    })
    .then(({ url }) => {
      const target = new URL(url);
      const expectedHost = `windmill.${window.location.hostname}`;
      if (target.host !== expectedHost || !target.pathname.startsWith("/public/dhr/")) {
        throw new Error("embed configuration contains an unexpected Windmill URL");
      }
      frame.addEventListener("load", () => frame.classList.add("is-loaded"), { once: true });
      frame.src = target.href;
    })
    .catch((error) => {
      console.error("Could not load the objective app", error);
      frame.replaceWith(Object.assign(document.createElement("p"), {
        className: "embed-error",
        textContent: "The objective app is not available yet. Ask an administrator to refresh the deployment.",
      }));
    });
})();
