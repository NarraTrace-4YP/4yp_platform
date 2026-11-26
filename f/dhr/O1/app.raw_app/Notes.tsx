import React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
export default function NotesTab() {



    const markdown = `


## Heading
This is the notes






`;





    return (
        <div className="page">
            <main className="notes-content">
                <Markdown remarkPlugins={[remarkGfm]}>{markdown}</Markdown>
            </main>
        </div>
    );
}