package com.example.speako.domain.audio.controller;

import com.example.speako.domain.audio.dto.TtsRequestDto;
import com.example.speako.domain.audio.dto.TtsResponseDto;
import com.example.speako.domain.audio.service.AudioService;
import com.example.speako.global.common.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/audio")
@RequiredArgsConstructor
public class AudioController {

    private final AudioService audioService;

    /**
     * 1. 전체 대본 음성 듣기 API
     */
    @PostMapping("/tts/full")
    public ResponseEntity<ApiResponse<TtsResponseDto>> getFullScriptTts(
            @RequestBody TtsRequestDto.FullTtsDto request
    ) {
        TtsResponseDto response = audioService.generateFullScriptAudio(request);
        return ResponseEntity.ok(ApiResponse.onSuccess(response));
    }

    /**
     * 2. 특정 하이라이팅 단어 음성 듣기 API
     */
    @PostMapping("/tts/highlight")
    public ResponseEntity<ApiResponse<TtsResponseDto>> getHighlightTts(
            @RequestBody TtsRequestDto.HighlightTtsDto request
    ) {
        TtsResponseDto response = audioService.generateHighlightAudio(request);
        return ResponseEntity.ok(ApiResponse.onSuccess(response));
    }
}