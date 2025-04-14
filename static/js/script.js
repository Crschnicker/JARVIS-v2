// --- START OF IMPROVED script.js ---
document.addEventListener('DOMContentLoaded', () => {
    const chatbox = document.getElementById('chatbox');
    const userInput = document.getElementById('userInput');
    const sendButton = document.getElementById('sendButton');
    const voiceButton = document.getElementById('voiceButton');
    const startOverButton = document.getElementById('startOverButton');
    const statusDiv = document.getElementById('status');
    const fileUploadButton = document.getElementById('fileUploadButton');
    const fileInput = document.getElementById('fileInput');

    // Keep track of active streams
    let activeStream = null;
    let fullStreamResponse = '';

    // --- Speech Recognition Setup ---
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
            voiceButton.classList.add('listening'); 
            statusDiv.textContent = 'Listening...'; 
            voiceButton.innerHTML = '<i class="fas fa-stop"></i>'; 
        };
        
        recognition.onresult = (event) => { 
            userInput.value = event.results[0][0].transcript; 
        };
        
        recognition.onend = () => { 
            if (isListening) { 
                isListening = false; 
                voiceButton.classList.remove('listening'); 
                statusDiv.textContent = ''; 
                voiceButton.innerHTML = '<i class="fas fa-microphone"></i>'; 
                if (userInput.value.trim() !== '') { 
                    sendMessage(); 
                } 
            } 
        };
        
        recognition.onerror = (event) => { 
            console.error('Speech error:', event.error); 
            statusDiv.textContent = `Error: ${event.error}`; 
            if (isListening) { 
                isListening = false; 
                voiceButton.classList.remove('listening'); 
                voiceButton.innerHTML = '<i class="fas fa-microphone"></i>'; 
            } 
        };
    } else { 
        console.warn('Speech Recognition not supported.'); 
        if(voiceButton) { 
            voiceButton.disabled = true; 
            voiceButton.title = 'Voice input not supported'; 
            statusDiv.textContent = 'Voice input unavailable.'; 
        } 
    }

    // --- Speech Synthesis Setup ---
    const synth = window.speechSynthesis;
    function speak(text) { 
        if (synth && text) { 
            if (synth.speaking) { 
                return; 
            } 
            const utterThis = new SpeechSynthesisUtterance(text); 
            utterThis.onerror = (event) => console.error('Speech synth error', event); 
            synth.speak(utterThis); 
        } 
    }

    // --- Streaming Functions ---
    function closeActiveStream() {
        if (activeStream) {
            activeStream.close();
            activeStream = null;
            fullStreamResponse = '';
        }
    }

    async function startStreaming(streamId) {
        // Close any existing stream
        closeActiveStream();
        
        // Get a reference to the last bot message (placeholder)
        const botMessages = chatbox.querySelectorAll('.bot-message');
        const lastBotMessage = botMessages[botMessages.length - 1];
        if (!lastBotMessage) return;
        
        try {
            const evtSource = new EventSource(`/stream/${streamId}`);
            activeStream = evtSource;
            
            evtSource.onmessage = function(event) {
                const data = JSON.parse(event.data);
                
                // Handle chunk data
                if (data.chunk !== undefined) {
                    // Append to full response
                    fullStreamResponse += data.chunk;
                    
                    // Update the displayed message with what we have so far
                    // Basic formatting for streaming response
                    const formattedText = formatBotMessage(fullStreamResponse);
                    lastBotMessage.innerHTML = formattedText;
                    
                    // Scroll to bottom
                    chatbox.scrollTop = chatbox.scrollHeight;
                }
                
                // Handle completion
                if (data.done) {
                    evtSource.close();
                    activeStream = null;
                }
                
                // Handle errors
                if (data.error) {
                    console.error('Streaming error:', data.error);
                    evtSource.close();
                    activeStream = null;
                    
                    // Show error message
                    lastBotMessage.innerHTML = `Error: ${data.error}`;
                }
            };
            
            evtSource.onerror = function(err) {
                console.error('EventSource error:', err);
                evtSource.close();
                activeStream = null;
                
                // Update message to show error
                lastBotMessage.innerHTML = 'Error: Connection lost while streaming response.';
            };
        } catch (error) {
            console.error('Error starting stream:', error);
            // Update message to show error
            lastBotMessage.innerHTML = 'Error: Failed to start streaming response.';
        }
    }

    // --- Message Formatting ---
    function formatBotMessage(text) {
        if (!text) return '';
        
        // Basic sanitization
        let formatted = text
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            
        // Convert markdown-style formatting
        formatted = formatted
            // Bold text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            // Italic text
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            // Headers
            .replace(/^### (.*?)$/gm, '<h3>$1</h3>')
            .replace(/^## (.*?)$/gm, '<h2>$1</h2>')
            .replace(/^# (.*?)$/gm, '<h1>$1</h1>')
            
        // Convert line breaks
        formatted = formatted.replace(/\n/g, '<br>');
            
        // Convert bullet lists
        const bulletPattern = /- (.*?)(?=<br>- |$)/gs;
        if (formatted.match(bulletPattern)) {
            formatted = formatted.replace(bulletPattern, (match, content) => {
                return `<li>${content}</li>`;
            });
            formatted = formatted.replace(/<li>/g, '<ul><li>').replace(/<\/li>/g, '</li></ul>');
            formatted = formatted.replace(/<\/ul><ul>/g, '');
        }
            
        // Convert numbered lists
        const numberedPattern = /\d+\. (.*?)(?=<br>\d+\. |$)/gs;
        if (formatted.match(numberedPattern)) {
            formatted = formatted.replace(numberedPattern, (match, content) => {
                return `<li>${content}</li>`;
            });
            formatted = formatted.replace(/<li>/g, '<ol><li>').replace(/<\/li>/g, '</li></ol>');
            formatted = formatted.replace(/<\/ol><ol>/g, '');
        }
            
        return formatted;
    }

    // --- Chat Functions ---
    function addMessage(message, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', `${sender}-message`);
        
        if (sender === 'bot') {
            // Apply message formatting for bot messages
            messageDiv.innerHTML = formatBotMessage(message);
        } else {
            // For user messages, just escape HTML and handle newlines
            messageDiv.textContent = message;
        }
        
        chatbox.appendChild(messageDiv);
        chatbox.scrollTop = chatbox.scrollHeight;
    }

    async function sendMessage() {
        const messageText = userInput.value.trim();
        if (!messageText) return;
        
        // Close any active stream before sending a new message
        closeActiveStream();
        
        addMessage(messageText, 'user');
        userInput.value = '';
        userInput.disabled = true;
        sendButton.disabled = true;
        if(voiceButton) voiceButton.disabled = true;
        if(fileUploadButton) fileUploadButton.disabled = true;
        
        statusDiv.textContent = 'Thinking...';
        
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
                } catch (e) { /* ignore json parsing error */ }
                
                if (response.status === 401) {
                    errorMsg = "Session may have expired. Please log in again.";
                    // Optional redirect
                    window.location.href = '/login';
                }
                throw new Error(errorMsg);
            }
            
            const data = await response.json();
            
            // Check if streaming is required
            if (data.streaming && data.stream_id) {
                // Add initial placeholder message
                addMessage(data.response, 'bot');
                // Start streaming for updates
                startStreaming(data.stream_id);
            } else {
                // Regular non-streaming response
                addMessage(data.response, 'bot');
                speak(data.response);
            }
        } catch (error) {
            console.error('Error sending/receiving message:', error);
            addMessage(`Error: ${error.message}`, 'bot');
            speak(`Sorry, there was an error: ${error.message}`);
        } finally {
            statusDiv.textContent = '';
            userInput.disabled = false;
            sendButton.disabled = false;
            if(voiceButton) voiceButton.disabled = false;
            if(fileUploadButton) fileUploadButton.disabled = false;
            userInput.focus();
        }
    }

    async function sendMessageWithImage(file) {
        // Close any active stream
        closeActiveStream();
        
        const messageText = userInput.value.trim();
        const formData = new FormData();
        
        formData.append('image', file);
        formData.append('message', messageText);
        
        // Add the message to the chat (include file name)
        const displayMessage = messageText ? 
            `${messageText} [Uploaded: ${file.name}]` : 
            `[Uploaded: ${file.name}]`;
        
        addMessage(displayMessage, 'user');
        userInput.value = '';
        userInput.disabled = true;
        sendButton.disabled = true;
        if(voiceButton) voiceButton.disabled = true;
        if(fileUploadButton) fileUploadButton.disabled = true;
        
        statusDiv.textContent = 'Analyzing image...';
        
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
                } catch (e) { /* ignore json parsing error */ }
                
                throw new Error(errorMsg);
            }
            
            const data = await response.json();
            addMessage(data.response, 'bot');
            speak(data.response);
            
        } catch (error) {
            console.error('Error processing image:', error);
            addMessage(`Error: ${error.message}`, 'bot');
            speak(`Sorry, there was an error processing the image: ${error.message}`);
        } finally {
            statusDiv.textContent = '';
            userInput.disabled = false;
            sendButton.disabled = false;
            if(voiceButton) voiceButton.disabled = false;
            if(fileUploadButton) fileUploadButton.disabled = false;
            userInput.focus();
        }
    }

    async function resetConversation() {
        if (!confirm("Are you sure you want to start a new conversation? The current context will be lost.")) {
            return;
        }
        
        // Close any active stream
        closeActiveStream();
        
        statusDiv.textContent = 'Resetting...';
        try {
            const response = await fetch('/reset_chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error ${response.status}`);
            }
            
            const data = await response.json();
            chatbox.innerHTML = '';
            addMessage(data.message || "Conversation reset. How can I help?", 'bot');
            speak(data.message || "Conversation reset.");
            userInput.value = '';
            statusDiv.textContent = '';
        } catch (error) {
            console.error('Error resetting conversation:', error);
            statusDiv.textContent = `Reset Error: ${error.message}`;
            addMessage(`Error resetting conversation: ${error.message}`, 'bot');
        }
    }

    // --- Event Listeners ---
    sendButton.addEventListener('click', sendMessage);
    
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
    
    if (voiceButton && recognition) {
        voiceButton.addEventListener('click', () => {
            if (!isListening) {
                recognition.start();
            } else {
                isListening = false;
                recognition.stop();
                voiceButton.classList.remove('listening');
                statusDiv.textContent = '';
                voiceButton.innerHTML = '<i class="fas fa-microphone"></i>';
                if (userInput.value.trim() !== '') {
                    sendMessage();
                }
            }
        });
    }
    
    if(startOverButton) {
        startOverButton.addEventListener('click', resetConversation);
    }
    
    // File upload handling
    if (fileUploadButton && fileInput) {
        fileUploadButton.addEventListener('click', () => {
            fileInput.click();
        });
        
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length > 0) {
                const file = fileInput.files[0];
                // Check if file is an image
                if (file.type.startsWith('image/')) {
                    sendMessageWithImage(file);
                } else {
                    alert('Please select an image file.');
                }
            }
        });
    }

    // Scroll to bottom on initial load if there's history
    chatbox.scrollTop = chatbox.scrollHeight;
});
// --- END OF IMPROVED script.js ---