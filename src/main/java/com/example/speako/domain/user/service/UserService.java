package com.example.speako.domain.user.service;

import com.example.speako.domain.user.dto.UserRequestDTO;
import com.example.speako.domain.user.dto.UserResponseDTO;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.repository.UserRepository;
import com.example.speako.global.config.JwtTokenProvider;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class UserService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;

    @Transactional
    public User signUp(UserRequestDTO.SignUpDTO request) {
        // 1. 비밀번호 일치 검증
        if (!request.getPassword().equals(request.getPasswordCheck())) {
            throw new IllegalArgumentException("입력하신 비밀번호와 비밀번호 확인이 일치하지 않습니다.");
        }

        // 2. 이메일 중복 검증
        userRepository.findByEmail(request.getEmail())
                .ifPresent(user -> {
                    throw new IllegalArgumentException("이미 사용 중인 이메일입니다.");
                });

        // 3. 닉네임 중복 검증 (조건 6)
        if (userRepository.existsByName(request.getName())) {
            throw new IllegalArgumentException("이미 사용 중인 닉네임입니다.");
        }

        // 4. 비밀번호 BCrypt 암호화
        String encodedPassword = passwordEncoder.encode(request.getPassword());

        // 5. 유저 엔티티 생성 및 저장
        User newUser = User.builder()
                .email(request.getEmail())
                .password(encodedPassword)
                .name(request.getName())
                .build();

        return userRepository.save(newUser);
    }

        @Transactional
        public UserResponseDTO.LoginResultDTO login(UserRequestDTO.LoginDTO request) {
            // 1. 이메일로 유저 조회
            User user = userRepository.findByEmail(request.getEmail())
                    .orElseThrow(() -> new IllegalArgumentException("가입되지 않은 이메일입니다."));

            // 2. 비밀번호 검증
            if (!passwordEncoder.matches(request.getPassword(), user.getPassword())) {
                throw new IllegalArgumentException("비밀번호가 일치하지 않습니다.");
            }

            // 3. 인증 성공 시 JWT 토큰 생성
            String accessToken = jwtTokenProvider.createAccessToken(user.getUserId(), user.getEmail());

            return UserResponseDTO.LoginResultDTO.builder()
                    .userId(user.getUserId())
                    .email(user.getEmail())
                    .name(user.getName())
                    .accessToken(accessToken)
                    .build();
        }

        //이름 수정
        // 이름 수정
        @Transactional
        public void updateName(Long userId, UserRequestDTO.UpdateNameDTO request) { // 👈 파라미터 Long userId로 변경
            User user = userRepository.findById(userId) // 👈 findByEmail 대신 findById 사용!
                    .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

            // 닉네임 중복 검증
            if (userRepository.existsByName(request.getName())) {
                throw new IllegalArgumentException("이미 사용 중인 닉네임입니다.");
            }

            user.updateName(request.getName());
        }

        //이메일 변경
        // 이메일 변경
        @Transactional
        public void updateEmail(Long userId, UserRequestDTO.UpdateEmailDTO request) { // 👈 파라미터 Long userId로 변경
            User user = userRepository.findById(userId) // 👈 findByEmail 대신 findById 사용!
                    .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

            // 현재 비밀번호 검증
            if (!passwordEncoder.matches(request.getCurrentPassword(), user.getPassword())) {
                throw new IllegalArgumentException("현재 비밀번호가 일치하지 않습니다.");
            }

            // 새 이메일이 기존 이메일과 다를 때만 중복 검증 수행
            if (!user.getEmail().equals(request.getNewEmail())) {
                if (userRepository.findByEmail(request.getNewEmail()).isPresent()) {
                    throw new IllegalArgumentException("이미 사용 중인 이메일입니다.");
                }
            }

            user.updateEmail(request.getNewEmail());
        }
    //비밀번호 변경
    @Transactional
    public void updatePassword(Long userId, UserRequestDTO.UpdatePasswordDTO request) {
        User user = userRepository.findById(userId) // 👈 findByEmail 대신 findById 사용!
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

        if (!passwordEncoder.matches(request.getCurrentPassword(), user.getPassword())) {
            throw new IllegalArgumentException("현재 비밀번호가 일치하지 않습니다.");
        }

        if (!request.getNewPassword().equals(request.getNewPasswordCheck())) {
            throw new IllegalArgumentException("새로 입력한 비밀번호와 확인이 일치하지 않습니다.");
        }

        String encodedNewPassword = passwordEncoder.encode(request.getNewPassword());
        user.updatePassword(encodedNewPassword);
    }

    // 회원 탈퇴 (userId 기반)
    @Transactional
    public void withdraw(Long userId, String password) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

        if (!passwordEncoder.matches(password, user.getPassword())) {
            throw new IllegalArgumentException("비밀번호가 일치하지 않습니다.");
        }

        userRepository.delete(user);
    }

    // 로그아웃 (userId 기반)
    @Transactional
    public void logout(Long userId) {
        // JWT Stateless 환경에서는 클라이언트 토큰 삭제 처리
    }
}

