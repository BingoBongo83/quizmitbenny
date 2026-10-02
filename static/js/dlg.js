/* Styled modal dialogs replacing native alert/confirm/prompt.
   await Dlg.alert("msg")                 -> resolves on OK
   await Dlg.confirm("msg")               -> resolves true/false
   await Dlg.prompt("msg", "20")          -> resolves string or null (cancel)
   opts for prompt: { title, type: "number"|"text" } */
window.Dlg = (function () {
  let overlay = null;

  function build() {
    overlay = document.createElement("div");
    overlay.className = "modal-overlay dlg-overlay hidden";
    overlay.innerHTML = `
      <div class="modal dlg-box">
        <h2 class="dlg-title"></h2>
        <div class="dlg-msg"></div>
        <input class="dlg-input hidden">
        <div class="modal-foot"></div>
      </div>`;
    document.body.appendChild(overlay);
  }

  function show({ title, message, inputType, def, okLabel, cancelLabel }) {
    if (!overlay) build();
    const box = overlay.querySelector(".dlg-box");
    const inp = box.querySelector(".dlg-input");
    const foot = box.querySelector(".modal-foot");
    box.querySelector(".dlg-title").textContent = title;
    box.querySelector(".dlg-msg").textContent = message;
    inp.classList.toggle("hidden", inputType == null);
    if (inputType) {
      inp.type = inputType;
      inp.value = def || "";
    }
    foot.innerHTML = "";
    overlay.classList.remove("hidden");

    return new Promise((resolve) => {
      const close = (val) => {
        overlay.classList.add("hidden");
        document.removeEventListener("keydown", onKey);
        resolve(val);
      };
      const getOk = () => (inputType ? inp.value : true);
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); close(null); }
        else if (e.key === "Enter") { e.preventDefault(); close(getOk()); }
      };
      if (cancelLabel != null) {
        const cb = document.createElement("button");
        cb.textContent = cancelLabel;
        cb.onclick = () => close(null);
        foot.appendChild(cb);
      }
      const ob = document.createElement("button");
      ob.textContent = okLabel || "OK";
      ob.className = "primary";
      ob.onclick = () => close(getOk());
      foot.appendChild(ob);
      document.addEventListener("keydown", onKey);
      if (inputType) { inp.focus(); inp.select(); } else ob.focus();
    });
  }

  return {
    alert: (message, title) =>
      show({ title: title || "Hinweis", message, okLabel: "OK" }),
    confirm: async (message, title) =>
      (await show({
        title: title || "Bestätigen", message,
        okLabel: "OK", cancelLabel: "Abbrechen",
      })) === true,
    prompt: (message, def, opts) =>
      show({
        title: (opts && opts.title) || "Eingabe", message,
        inputType: (opts && opts.type) || "text", def,
        okLabel: "OK", cancelLabel: "Abbrechen",
      }),
  };
})();
