require('dotenv').config();

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { GoogleGenerativeAI } = require('@google/generative-ai');

// 1. Initialize Gemini
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);
// We use gemini-1.5-flash as it is the fastest and most cost-effective model
const model = genAI.getGenerativeModel({ model: "gemini-3.5-flash" });

// 2. Initialize WhatsApp Client
// LocalAuth saves your session so you don't have to scan the QR code every time
const client = new Client({
    authStrategy: new LocalAuth(),
});

// 3. Generate QR Code in Terminal
client.on('qr', (qr) => {
    console.log("Scan this QR code in WhatsApp to log in:");
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log('Bot is ready and listening to messages!');
});

// 4. Listen for Messages and Respond
client.on('message', async (message) => {
    // Ignore statuses and group chats for now
    if (message.isStatus || message.from.includes('@g.us')) return;

    console.log(`Received message... processing.`);

    try {
        // By default, our prompt is just the text they sent
        let promptContent = [message.body];

        // 1. Check if the user attached an image/document
        if (message.hasMedia) {
            // We manually map it back to _serialized so the library can find the image.
            if (message.id && !message.id._serialized && message.id.$1) {
                message.id._serialized = message.id.$1;
            }
            
            // 2. Download the media from WhatsApp
            const media = await message.downloadMedia();
            
            if (media) {
                console.log(`Downloaded media: ${media.mimetype}`);
                
                // 3. Format the image exactly how Gemini expects it (Base64)
                const imagePart = {
                    inlineData: {
                        data: media.data,
                        mimeType: media.mimetype
                    }
                };
                
                // 4. Combine the text caption and the image
                // If they just sent an image with no text, we provide a default prompt
                const textPrompt = message.body || "Please describe or solve what is in this image.";
                
                promptContent = [textPrompt, imagePart];
            }
        }

        // 5. Send the combined content (Text + Image) to Gemini
        const result = await model.generateContent(promptContent);
        const aiResponse = result.response.text();
        
        await message.reply(aiResponse);
        console.log("Replied successfully.");
        
    } catch (error) {
        console.error("Error generating response:", error);
        await message.reply("Sorry, I ran into an error processing that.");
    }
});

// Start the client
client.initialize();