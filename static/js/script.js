// --- START OF FULL UPDATED script.js ---
document.addEventListener('DOMContentLoaded', () => {
    const chatbox = document.getElementById('chatbox');
    // --- MODIFIED: Get textarea element ---
    const userInput = document.getElementById('userInput');
    // --- END MODIFICATION ---
    const sendButton = document.getElementById('sendButton');
    const voiceButton = document.getElementById('voiceButton'); // Input voice button
    const startOverButton = document.getElementById('startOverButton');
    const statusDiv = document.getElementById('status');
    const fileUploadButton = document.getElementById('fileUploadButton');
    const fileInput = document.getElementById('fileInput');
    const toggleVoiceButton = document.getElementById('toggleVoiceButton');
    const voiceIcon = document.getElementById('voiceIcon');


    // Keep track of active streams
    let activeStream = null;
    let fullStreamResponse = '';
    let streamedBotMessageElement = null; // Keep reference to the element being updated

    // --- Speech Recognition Setup (Voice Input) ---
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let recognition;
    let isListening = false;
    if (SpeechRecognition) {
        recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.lang = 'en-US';
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;

        recognition.onstart = () => {
            isListening = true;
            if (voiceButton) voiceButton.classList.add('listening');
            statusDiv.textContent = 'Listening...';
            if (voiceButton) voiceButton.innerHTML = '<i class="fas fa-stop"></i>';
        };

        recognition.onresult = (event) => {
            // Append recognized text to textarea instead of replacing? Or replace? Current: Replace.
            userInput.value = event.results[0][0].transcript;
            autoResizeTextarea(); // Adjust height after inserting text
            // Optionally trigger send automatically:
            // recognition.stop(); // Stop listening
            // sendMessage();
        };

        recognition.onend = () => {
             if (isListening) {
                isListening = false;
                if (voiceButton) voiceButton.classList.remove('listening');
                statusDiv.textContent = '';
                if (voiceButton) voiceButton.innerHTML = '<i class="fas fa-microphone"></i>';
             }
        };

        recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            statusDiv.textContent = `Recog Error: ${event.error}`;
            if (isListening) {
                isListening = false;
                if (voiceButton) voiceButton.classList.remove('listening');
                if (voiceButton) voiceButton.innerHTML = '<i class="fas fa-microphone"></i>';
            }
        };
    } else {
        console.warn('Speech Recognition not supported.');
        if(voiceButton) {
            voiceButton.disabled = true;
            voiceButton.title = 'Voice input not supported';
        }
    }

    // --- Speech Synthesis Setup (Voice Output) ---
    const synth = window.speechSynthesis;
    let voiceEnabled = false; // Default state initialized later from localStorage

    /**
     * Cleans text and initiates speech synthesis if enabled.
     * Handles cancelling previous speech.
     * @param {string} text The text to speak.
     */
    function speak(text) {
        if (!voiceEnabled || !synth || !text) {
            // console.log("Speak called but voice disabled, synth unavailable, or no text."); // Reduce noise
            return;
        }

        const cleanText = text
            .replace(/```[\s\S]*?```/g, 'Code block')
            .replace(/`([^`]+)`/g, '$1')
            .replace(/(\*\*|__)(.*?)\1/g, '$2')
            .replace(/(\*|_)(.*?)\1/g, '$2')
            .replace(/\[(.*?)\]\(.*?\)/g, '$1')
            .replace(/<br\s*\/?>/gi, '\n') // Convert HTML breaks to newlines for speech pacing
            .replace(/<h[1-6]>(.*?)<\/h[1-6]>/gi, '$1. ') // Add punctuation after headers
            .replace(/<li[^>]*>(.*?)<\/li>/gi, '$1. ') // Treat list items as sentences
            .replace(/<[^>]+>/g, '') // Strip remaining HTML tags
            .replace(/</g, '<').replace(/>/g, '>').replace(/&/g, '&') // Handle common entities
            .trim();

        if (!cleanText) {
             // console.log("Speak called but text became empty after cleaning."); // Reduce noise
             return;
        }

        if (synth.speaking) {
            console.log("Synth is speaking, cancelling previous utterance before speaking new text.");
            synth.cancel();
            setTimeout(() => speakNow(cleanText), 100);
            return;
        }
        speakNow(cleanText);
    }

    /**
     * Creates and speaks the utterance. Called by speak() directly or via setTimeout.
     * @param {string} text The cleaned text to speak.
     */
    function speakNow(text) {
         if (!voiceEnabled || !text || !synth) {
              // console.log("speakNow called but voice disabled, no text, or synth unavailable."); // Reduce noise
              return;
         }

         const utterance = new SpeechSynthesisUtterance(text);
         // Optional: Configure voice, rate, pitch
         // utterance.voice = ...
         // utterance.rate = 1;
         // utterance.pitch = 1;

         utterance.onerror = (event) => {
             console.error('SpeechSynthesisUtterance.onerror', event);
             if (event.error === 'interrupted') {
                 console.log("Speech was interrupted (expected behavior).");
             } else if (event.error === 'canceled') {
                 console.log("Speech was canceled (expected behavior).");
             } else if (event.error === 'network') {
                 addMessage("Speech error: Could not fetch voice.", "system");
             } else {
                 addMessage(`Speech error: ${event.error}`, 'system'); // Use system message type
             }
         };

         if(synth.speaking) {
              console.warn("Synth still speaking despite cancel attempt? Aborting new speech in speakNow.");
              return;
         }

         console.log("Attempting to speak:", text.substring(0, 50) + "...");
         try {
            synth.speak(utterance);
         } catch (e) {
             console.error("Error calling synth.speak:", e);
             addMessage(`Error initiating speech: ${e.message}`, 'system');
         }
    }

    // --- Voice Toggle Functions ---
    function updateVoiceButton() {
        if (!toggleVoiceButton || !voiceIcon) return;
        if (voiceEnabled) {
            voiceIcon.classList.remove('fa-volume-mute');
            voiceIcon.classList.add('fa-volume-up');
            toggleVoiceButton.title = 'Disable Voice Responses';
            toggleVoiceButton.setAttribute('aria-pressed', 'true');
        } else {
            voiceIcon.classList.remove('fa-volume-up');
            voiceIcon.classList.add('fa-volume-mute');
            toggleVoiceButton.title = 'Enable Voice Responses';
            toggleVoiceButton.setAttribute('aria-pressed', 'false');
        }
    }

    function toggleVoice() {
        voiceEnabled = !voiceEnabled;
        localStorage.setItem('voiceResponseEnabled', voiceEnabled);
        updateVoiceButton();

        if (!voiceEnabled && synth.speaking) {
            console.log("Voice responses disabled by user toggle, stopping current speech (expect 'interrupted' or 'canceled' error).");
            synth.cancel();
        }
        addMessage(`Voice responses ${voiceEnabled ? 'enabled' : 'disabled'}.`, 'system');
        console.log(`Voice responses ${voiceEnabled ? 'enabled' : 'disabled'}`);
    }

    // --- Auto-resize Textarea Function ---
    function autoResizeTextarea() {
        // Reset height to auto to get the correct scrollHeight
        userInput.style.height = 'auto';
        // Set height based on scroll height
        userInput.style.height = userInput.scrollHeight + 'px';

        // Optional: Limit max height
        const maxHeight = 150; // Example max height in pixels
        if (userInput.scrollHeight > maxHeight) {
            userInput.style.height = maxHeight + 'px';
            userInput.style.overflowY = 'auto'; // Show scrollbar if max height is reached
        } else {
            userInput.style.overflowY = 'hidden'; // Hide scrollbar if below max height
        }
    }

    // --- Streaming Functions ---
    function closeActiveStream() {
        if (activeStream) {
            activeStream.close();
            activeStream = null;
            fullStreamResponse = '';
            streamedBotMessageElement = null;
            console.log("SSE stream closed.");
        }
    }

    async function startStreaming(streamId) {
        closeActiveStream();

        const botMessages = chatbox.querySelectorAll('.bot-message');
        streamedBotMessageElement = botMessages[botMessages.length - 1];

        if (!streamedBotMessageElement) {
            console.error("Could not find placeholder message element for streaming.");
            return;
        }

        streamedBotMessageElement.innerHTML = '<span class="thinking-indicator"></span>'; // Show subtle indicator
        fullStreamResponse = '';

        try {
            const evtSource = new EventSource(`/stream/${streamId}`);
            activeStream = evtSource;

            evtSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);

                    if (data.chunk !== undefined) {
                        fullStreamResponse += data.chunk;
                        streamedBotMessageElement.innerHTML = formatBotMessage(fullStreamResponse);
                        chatbox.scrollTop = chatbox.scrollHeight;
                    }

                    if (data.done) {
                        console.log("Streaming finished.");
                        closeActiveStream();
                        if (streamedBotMessageElement) {
                           streamedBotMessageElement.innerHTML = formatBotMessage(fullStreamResponse);
                        }
                        speak(fullStreamResponse);
                        fullStreamResponse = '';
                        streamedBotMessageElement = null;
                    }

                    if (data.error) {
                        console.error('Streaming error message:', data.error);
                        if (streamedBotMessageElement) {
                           streamedBotMessageElement.innerHTML = formatBotMessage(fullStreamResponse) + // Show completed part
                                `<br><strong style="color:red;">Error: ${formatBotMessage(data.error)}</strong>`;
                        }
                        closeActiveStream();
                         fullStreamResponse = '';
                        streamedBotMessageElement = null;
                    }
                } catch (parseError) {
                    console.error("Failed to parse stream data:", parseError, "Raw data:", event.data);
                    if (streamedBotMessageElement) {
                        streamedBotMessageElement.innerHTML += `<br><strong style="color:orange;">Error processing stream update.</strong>`;
                    }
                    // Close stream on parse error? Maybe safer.
                    closeActiveStream();
                }
            };

            evtSource.onerror = function(err) {
                console.error('EventSource failed:', err);
                if (streamedBotMessageElement) {
                    // Show error within the message element if possible
                     streamedBotMessageElement.innerHTML = formatBotMessage(fullStreamResponse) +
                         '<br><strong style="color:red;">Error: Connection lost during stream.</strong>';
                } else {
                    // If element is gone, add new message
                    addMessage("Error: Connection lost during stream.", "system");
                }
                speak("Sorry, the connection was lost.");
                closeActiveStream();
                 fullStreamResponse = '';
                streamedBotMessageElement = null;
            };
        } catch (error) {
            console.error('Error starting EventSource:', error);
            if (streamedBotMessageElement) {
                streamedBotMessageElement.innerHTML = 'Error: Could not start streaming response.';
            }
            speak("Sorry, I couldn't start streaming the response.");
             fullStreamResponse = '';
            streamedBotMessageElement = null;
        }
    }


    // --- Message Formatting ---
    function formatBotMessage(text) {
        if (typeof text !== 'string' || !text) return '';

        let formatted = text
            .replace(/&/g, "&") // Escape ampersands first
            .replace(/</g, "<")
            .replace(/>/g, ">");

        formatted = formatted
             .replace(/```(\w+)?\s*?\n([\s\S]*?)\n```/g, (match, lang, code) => {
                 const languageClass = lang ? `language-${lang.replace(/</g, '<').replace(/>/g, '>')}` : ''; // Unescape lang if needed
                 const escapedCode = code; // Already escaped < > & above
                 return `<pre><code class="${languageClass}">${escapedCode.trim()}</code></pre>`;
             })
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)/g, '<em>$1</em>')
            .replace(/(?<!_)_(?!_)(.*?)(?<!_)_(?!_)/g, '<em>$1</em>')
            .replace(/^### (.*?)$/gm, '<h3>$1</h3>')
            .replace(/^## (.*?)$/gm, '<h2>$1</h2>')
            .replace(/^# (.*?)$/gm, '<h1>$1</h1>')
            .replace(/\n/g, '<br>'); // Convert newlines after block elements

        // Convert lists (bullet and numbered) - Improved Logic
        const listRegex = /^(?:<br>)*\s*([-*]|\d+\.)\s(.*?)(?=\n(?:\s*([-*]|\d+\.)\s)|$)/gm;
        let lists = {};
        let listId = 0;

        formatted = formatted.replace(listRegex, (match, marker, item) => {
            const type = (marker === '-' || marker === '*') ? 'ul' : 'ol';
            const currentList = lists[listId];
            if (!currentList || currentList.type !== type) {
                listId++;
                lists[listId] = { type: type, items: [] };
            }
            lists[listId].items.push(item.trim());
            return `<!-- listitem ${listId} -->`; // Placeholder
        });

        // Replace placeholders with actual HTML lists
        for (const id in lists) {
            const list = lists[id];
            let listHtml = `<${list.type}>`;
            list.items.forEach(item => {
                listHtml += `<li>${item}</li>`; // item might contain inline markdown already processed
            });
            listHtml += `</${list.type}>`;
            // Replace only the first occurrence of the placeholder for this ID
            formatted = formatted.replace(`<!-- listitem ${id} -->`, listHtml);
        }
        // Remove remaining placeholders (if any)
        formatted = formatted.replace(/<!-- listitem \d+ -->/g, '');


        // Clean up extra <br> tags around blocks
        formatted = formatted.replace(/<br\s*\/?>\s*(<(ul|ol|li|h[1-6]|pre|code))/gi, '$1');
        formatted = formatted.replace(/(<\/(ul|ol|li|h[1-6]|pre|code))>(\s*<br\s*\/?>)+/gi, '$1');
        formatted = formatted.replace(/^(<br\s*\/?>)+/, ''); // Remove leading breaks

        return formatted;
    }

    // --- Chat Functions ---
    function addMessage(message, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', `${sender}-message`);

        // Sanitize and format based on sender
        if (sender === 'bot' || sender === 'model') { // Handle 'model' role from backend too
            messageDiv.innerHTML = formatBotMessage(message);
        } else if (sender === 'user') {
            // For user messages, escape HTML and preserve newlines
            const pre = document.createElement('pre');
            pre.style.whiteSpace = 'pre-wrap'; // Allow wrapping
            pre.style.wordWrap = 'break-word'; // Break long words
            pre.style.margin = '0';
            pre.style.fontFamily = 'inherit';
            pre.style.fontSize = 'inherit';
            pre.textContent = message; // Safely sets text content
            messageDiv.appendChild(pre);
        } else if (sender === 'system') {
            messageDiv.innerHTML = `<em>${message.replace(/</g, "<").replace(/>/g, ">")}</em>`;
             messageDiv.style.textAlign = 'center';
             messageDiv.style.fontSize = '0.9em';
             messageDiv.style.color = '#666';
        } else {
            // Default fallback (plain text)
            messageDiv.textContent = message;
        }

        chatbox.appendChild(messageDiv);
        chatbox.scrollTop = chatbox.scrollHeight;
        return messageDiv;
    }

    async function sendMessage() {
        // --- MODIFIED: Use textarea value ---
        const messageText = userInput.value.trim();
        // --- END MODIFICATION ---
        if (!messageText) return;

        if (synth.speaking) {
             console.log("User sent new message, cancelling any ongoing speech.");
             synth.cancel();
        }
        closeActiveStream();

        addMessage(messageText, 'user');
        userInput.value = ''; // Clear textarea
        autoResizeTextarea(); // Resize textarea back to default after sending
        setChatInputDisabled(true);
        statusDiv.textContent = 'Thinking...';
        // Optionally add spinner: statusDiv.innerHTML = '<span class="spinner"></span> Thinking...';

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ message: messageText }),
            });

            if (!response.ok) {
                let errorMsg = `HTTP error ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMsg = errorData.error || errorMsg;
                    if (errorData.redirect) {
                        addMessage("Redirecting...", "system");
                        window.location.href = errorData.redirect;
                        return;
                    }
                } catch (e) { /* ignore json parsing error */ }
                throw new Error(errorMsg);
            }

            const data = await response.json();

            if (data.streaming && data.stream_id) {
                 addMessage("", 'bot'); // Add placeholder (empty or with indicator)
                 startStreaming(data.stream_id);
             } else {
                 addMessage(data.response, 'bot');
                 speak(data.response);
             }
        } catch (error) {
            console.error('Error sending/receiving message:', error);
            const errorMessage = `Error: ${error.message}`;
            addMessage(errorMessage, 'bot'); // Show error in chat
             speak(`Sorry, there was an error: ${error.message}`);
        } finally {
            statusDiv.textContent = ''; // Clear status
            // statusDiv.innerHTML = ''; // Clear status including spinner
            setChatInputDisabled(false);
        }
    }

    async function sendMessageWithImage(file) {
        if (synth.speaking) synth.cancel();
        closeActiveStream();

        const messageText = userInput.value.trim(); // Get text potentially typed before selecting image
        const formData = new FormData();

        formData.append('image', file);
        if (messageText) {
            formData.append('message', messageText);
        }

        const displayMessageText = messageText
            ? `${messageText}\n[Uploaded: ${file.name}]` // Add newline before image info
            : `[Uploaded: ${file.name}]`;
        addMessage(displayMessageText, 'user');

        userInput.value = ''; // Clear text input after sending
        autoResizeTextarea(); // Reset textarea size
        setChatInputDisabled(true);
        statusDiv.textContent = 'Analyzing image...';
        // statusDiv.innerHTML = '<span class="spinner"></span> Analyzing image...';
        fileInput.value = ''; // Reset file input

        try {
            const response = await fetch('/chat_with_image', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                let errorMsg = `HTTP error ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMsg = errorData.error || errorMsg;
                     if (errorData.redirect) {
                        addMessage("Redirecting...", "system");
                        window.location.href = errorData.redirect;
                        return;
                    }
                } catch (e) { /* ignore */ }
                throw new Error(errorMsg);
            }

            const data = await response.json();
            addMessage(data.response, 'bot');
            speak(data.response);

        } catch (error) {
            console.error('Error processing image:', error);
            const errorMessage = `Error: ${error.message}`;
            addMessage(errorMessage, 'bot');
             speak(`Sorry, there was an error processing the image: ${error.message}`);
        } finally {
            statusDiv.textContent = '';
            // statusDiv.innerHTML = '';
            setChatInputDisabled(false);
        }
    }

    async function resetConversation() {
        if (!confirm("Are you sure you want to start a new conversation? The current context will be lost.")) {
            return;
        }

        if (synth.speaking) synth.cancel();
        closeActiveStream();

        statusDiv.textContent = 'Resetting...';
        // statusDiv.innerHTML = '<span class="spinner"></span> Resetting...';
        setChatInputDisabled(true);

        try {
            const response = await fetch('/reset_chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
            });

            if (!response.ok) {
                 let errorMsg = `HTTP error ${response.status}`;
                 try { errorMsg = (await response.json()).error || errorMsg } catch(e){}
                 throw new Error(errorMsg);
            }

            const data = await response.json();
            chatbox.innerHTML = ''; // Clear chat display
            const resetMsg = data.message || "Conversation reset. How can I help?";
            addMessage(resetMsg, 'bot');
            speak(resetMsg);
            userInput.value = '';
            autoResizeTextarea(); // Reset textarea size
            statusDiv.textContent = '';
            // statusDiv.innerHTML = '';
        } catch (error) {
            console.error('Error resetting conversation:', error);
            const errorMessage = `Reset Error: ${error.message}`;
            statusDiv.textContent = errorMessage; // Show error in status
            addMessage(errorMessage, 'system'); // Add system message
            speak(`Sorry, there was an error resetting the conversation.`);
        } finally {
             setChatInputDisabled(false);
        }
    }

    // Helper to disable/enable all input elements
    function setChatInputDisabled(disabled) {
        userInput.disabled = disabled;
        sendButton.disabled = disabled;
        if (voiceButton) voiceButton.disabled = disabled;
        if (fileUploadButton) fileUploadButton.disabled = disabled;
        // Keep Start Over and Voice Toggle generally enabled unless explicitly disabled elsewhere
        // if (startOverButton) startOverButton.disabled = disabled;
        // if (toggleVoiceButton) toggleVoiceButton.disabled = disabled;

        if (!disabled) {
            userInput.focus();
        } else {
            if (isListening && recognition) {
                 isListening = false;
                 recognition.stop();
            }
        }
    }


    // --- Initialization and Event Listeners ---

    // Load voice response preference
    const savedVoicePref = localStorage.getItem('voiceResponseEnabled');
    voiceEnabled = savedVoicePref === 'true';
    updateVoiceButton();


    sendButton.addEventListener('click', sendMessage);

    userInput.addEventListener('keydown', (e) => {
        // Check if the Enter key was pressed
        if (e.key === 'Enter') {
            // Check if the Shift key was NOT pressed
            if (!e.shiftKey) {
                // Prevent the default action (inserting a newline)
                e.preventDefault();
                // Send the message if the input is not disabled
                if (!userInput.disabled) {
                     sendMessage();
                }
            }
            // If Shift key WAS pressed, do nothing extra.
            // The default browser behavior for Shift+Enter in a textarea
            // is to insert a newline, which is what we want.
            // The autoResizeTextarea function will handle the height change
            // via the 'input' event listener already attached.
        }
        // The previous Ctrl+Enter/Cmd+Enter logic is removed as Enter alone now sends.
    });

    // --- Add listener for auto-resizing ---
    userInput.addEventListener('input', autoResizeTextarea);
    // --- END MODIFICATIONS ---

    if (voiceButton && recognition) {
        voiceButton.addEventListener('click', () => {
             if (userInput.disabled) return;
            if (!isListening) {
                 try {
                    // userInput.value = ''; // Optional: Clear input before listening
                    // autoResizeTextarea();
                    recognition.start();
                 } catch (err) {
                     if (err.name === 'NotAllowedError' || err.name === 'ServiceNotAllowedError') {
                         statusDiv.textContent = "Voice permission denied.";
                         addMessage("Voice input requires microphone permission. Please allow access.", "system");
                     } else {
                        console.error("Error starting recognition:", err);
                        statusDiv.textContent = "Error starting voice input.";
                     }
                     isListening = false; // Reset state
                     if (voiceButton) {
                         voiceButton.classList.remove('listening');
                         voiceButton.innerHTML = '<i class="fas fa-microphone"></i>';
                     }
                 }
            } else {
                 isListening = false;
                 recognition.stop();
                 // UI updates handled in onend
            }
        });
    }

    if(startOverButton) {
        startOverButton.addEventListener('click', resetConversation);
    }

    if(toggleVoiceButton) {
         toggleVoiceButton.addEventListener('click', toggleVoice);
    }

    if (fileUploadButton && fileInput) {
        fileUploadButton.addEventListener('click', () => {
             if (userInput.disabled) return;
            fileInput.click();
        });

        fileInput.addEventListener('change', (e) => {
             if (userInput.disabled) return;

            if (e.target.files.length > 0) {
                const file = e.target.files[0];
                if (file.type.startsWith('image/')) {
                    sendMessageWithImage(file);
                } else {
                    addMessage(`Selected file (${file.name}) is not a supported image type.`, 'system');
                    fileInput.value = ''; // Reset input
                }
            }
        });
    }

    // Initial setup
    chatbox.scrollTop = chatbox.scrollHeight; // Scroll to bottom
    autoResizeTextarea(); // Set initial height correctly
    setChatInputDisabled(false); // Ensure inputs are enabled

    // Add initial welcome message via JS if not rendered by Flask/needed
     const initialBotMessage = document.getElementById('initialBotMessage');
     if (!initialBotMessage) {
         const welcomeMsg = "Welcome! How can I assist you today?";
         addMessage(welcomeMsg, "bot");
         speak(welcomeMsg);
     } else {
         // If rendered by Flask, maybe still speak it?
         speak(initialBotMessage.textContent.trim());
     }


});
// --- END OF FULL UPDATED script.js ---